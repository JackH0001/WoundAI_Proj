package com.woundmeasurement.app.pipeline

import android.content.Context
import android.util.AtomicFile
import android.util.Base64
import com.google.gson.*
import com.woundmeasurement.app.data.crypto.PhiCrypto
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.MedicalDataScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest
import java.util.Date
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** Durable engine only. A caller must explicitly enqueue and provide fresh authorization per attempt.
 * No credentials, automatic classification, or reconstructed masks are persisted here.
 */
internal class ClinicalAnnotationOutbox(context: Context, private val scope: MedicalDataScope,
    private val now: () -> Long = System::currentTimeMillis) {
    internal val directory = File(context.applicationContext.noBackupFilesDir,"clinical_annotation_outbox/${scope.name.lowercase()}")
    private val mutex = locks.getOrPut(directory.canonicalPath) { Mutex() }
    private val processor = processors.getOrPut(directory.canonicalPath) { Mutex() }
    enum class State { PENDING, RETRY, PAUSED, RECEIPT_SAVED, COMPLETE, CANCELLED }
    enum class Permission { ALLOW, WAIT, REVOKED }
    data class Binding(val origin: String, val configuredUser: String, val actor: String)
    data class Response(val httpCode: Int, val body: String)
    data class Job(
        val schema: Int = 1, val id: String, val scope: String, val key: String,
        val binding: Binding, val rowJson: String, val rasterBase64: String,
        val imageSHA: String, val rasterSHA: String, val woundSHA: String, val tissueSHA: String,
        val createdAt: Long, val state: State = State.PENDING, val attempts: Int = 0,
        val nextAttemptAt: Long = 0, val issue: String? = null,
        val receiptID: String? = null, val receiptBody: String? = null,
        val localAcknowledged: Boolean = false
    ) {
        fun measurement(): MeasurementEntity = gson.fromJson(rowJson,MeasurementEntity::class.java)
        fun rasterBytes(): ByteArray = Base64.decode(rasterBase64,Base64.NO_WRAP)
    }

    /** Called only after the UI's explicit send confirmation and fresh identity/consent gate. */
    suspend fun enqueue(row: MeasurementEntity, binding: Binding, imageBytes: ByteArray,
        rasterBytes: ByteArray, current: suspend () -> MeasurementEntity?,
        savedAssets: suspend () -> Pair<ByteArray,ByteArray>?,
        permitted: suspend () -> Permission): Job = withContext(Dispatchers.IO) { mutex.withLock {
        val jobs = loadAll() // Any unreadable job stops new writes; never silently discard it.
        require(binding.origin.startsWith("https://") && binding.configuredUser.isNotBlank() && binding.actor.isNotBlank())
        require(row.id > 0 && row.doctorVerified && !row.wdCode.isNullOrBlank() && !row.imageId.isNullOrBlank())
        require(!row.annotationSubmitted && current() == row && permitted() == Permission.ALLOW)
        suspend fun assetsMatch(): Boolean {
            val actual=savedAssets() ?: return false
            return actual.first.contentEquals(imageBytes) && actual.second.contentEquals(rasterBytes)
        }
        require(assetsMatch())
        require(AnnotationProvenance.decode(row.annotationProvenance) != null)
        require(parsePolygons(row.gtPolygon).isNotEmpty())
        val image = android.graphics.BitmapFactory.decodeByteArray(imageBytes,0,imageBytes.size)
            ?: error("Saved image unavailable")
        val raster = try {
            require(image.width == row.imageW && image.height == row.imageH)
            requireNotNull(EditRasterCodec.decode(rasterBytes,row.rasterMeta,image.width,image.height))
        } finally { image.recycle() }
        require(raster.tissueEditCountKnown)
        val wound = requireNotNull(WoundMaskCodec.encode(raster.mask,raster.mw,raster.mh))
        val tissue = requireNotNull(TissueMaskCodec.encode(raster.tissue,raster.mask,raster.mw,raster.mh))
        val rowJson = gson.toJson(row.copy(annotationSubmitted = false))
        val imageSHA = sha(imageBytes); val rasterSHA = sha(rasterBytes)
        val key = sha(gson.toJson(listOf(scope.name,binding.origin,binding.configuredUser,binding.actor,rowJson,imageSHA,rasterSHA)).toByteArray(Charsets.UTF_8))
        val previous = jobs.firstOrNull { it.key == key && it.state != State.CANCELLED }
        if (previous != null) return@withLock previous
        val job = Job(id=UUID.randomUUID().toString(),scope=scope.name,key=key,binding=binding,rowJson=rowJson,
            rasterBase64=Base64.encodeToString(rasterBytes,Base64.NO_WRAP),imageSHA=imageSHA,rasterSHA=rasterSHA,
            woundSHA=sha(Base64.decode(wound,Base64.NO_WRAP)),tissueSHA=sha(Base64.decode(tissue,Base64.NO_WRAP)),createdAt=now())
        require(current() == row && assetsMatch() && permitted() == Permission.ALLOW)
        persist(job)
        job
    } }

    suspend fun jobs(): List<Job> = withContext(Dispatchers.IO) { mutex.withLock { loadAll() } }

    /** Serialized across instances. Receipt is durable before local acknowledgement.
     * WAIT/REVOKED never sends; a different account/server cannot inherit a queued doctor's work.
     * PAUSED needs an explicit retry after the caller has refreshed authentication or fixed the issue.
     */
    suspend fun process(id: String, binding: Binding, permitted: suspend (Job) -> Permission,
        upload: suspend (Job) -> Response, acknowledge: suspend (MeasurementEntity) -> Boolean,
        retryPaused: Boolean = false): Job = withContext(Dispatchers.IO) { processor.withLock process@ {
        var job = mutex.withLock { loadAll().first { it.id == id } }
        if (job.state in setOf(State.COMPLETE,State.CANCELLED)) return@process job
        if (job.binding != binding) return@process job // Do not overwrite another account's status.
        if (job.nextAttemptAt > now() && !retryPaused) return@process job
        if (job.state == State.PAUSED && !retryPaused) return@process job
        suspend fun update(next: Job): Job { mutex.withLock { persist(next) };job=next;return next }
        when(permitted(job)) {
            Permission.WAIT -> return@process update(job.copy(state=State.PAUSED,issue="authorization_required"))
            Permission.REVOKED -> return@process update(job.copy(state=State.CANCELLED,issue="permission_revoked"))
            Permission.ALLOW -> Unit
        }
        if (job.receiptID == null) {
            // Persist attempt before network I/O. Cancellation/crash leaves an exact, recoverable job.
            update(job.copy(state=State.RETRY,attempts=job.attempts+1,nextAttemptAt=now()+delayFor(job.attempts),issue="awaiting_receipt"))
            val response = try { upload(job) }
                catch(cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
                catch(_: Exception) { return@process update(job.copy(issue="transport_failed")) }
            val row=job.measurement()
            val valid=AnnotationReceipt.validate(response.httpCode,response.body,requireNotNull(row.wdCode),requireNotNull(row.imageId),job.woundSHA,job.tissueSHA).first
            if (!valid) {
                val retry=response.httpCode in setOf(408,429) || response.httpCode in 500..599
                return@process update(job.copy(state=if(retry) State.RETRY else State.PAUSED,
                    issue=if(retry) "server_retry" else "receipt_unconfirmed"))
            }
            // Store only the verified fields, never server error text or arbitrary returned data.
            val obj=JsonParser.parseString(response.body).asJsonObject
            val receipt=JsonObject().apply {
                listOf("status","code","image_id","annotation_receipt_id","mask_contract","wound_mask_sha256","tissue_mask_sha256")
                    .forEach { add(it,obj.get(it)) }
            }
            update(job.copy(state=State.RECEIPT_SAVED,receiptID=obj.get("annotation_receipt_id").asString,
                receiptBody=receipt.toString(),nextAttemptAt=0,issue=null))
        }
        when(permitted(job)) {
            Permission.WAIT -> return@process update(job.copy(state=State.PAUSED,issue="authorization_required"))
            Permission.REVOKED -> return@process update(job.copy(state=State.CANCELLED,issue="permission_revoked"))
            Permission.ALLOW -> Unit
        }
        val marked=try { acknowledge(job.measurement()) }
            catch(cancelled:kotlinx.coroutines.CancellationException) {throw cancelled}
            catch(_:Exception) {return@process update(job.copy(state=State.RECEIPT_SAVED,nextAttemptAt=now()+60_000,issue="local_ack_pending"))}
        update(job.copy(state=State.COMPLETE,localAcknowledged=marked,nextAttemptAt=0,
            issue=if(marked) null else "newer_local_revision_not_marked"))
    } }

    private fun loadAll(): List<Job> {
        if (!directory.exists()) return emptyList()
        val files=requireNotNull(directory.listFiles())
        // AtomicFile's pending .new is uncommitted; .bak is recovered via openRead.
        val names=files.map { it.name.removeSuffix(".bak") }.filter { it.endsWith(".enc") }.toSet()
        return names.map { name ->
            require(name.matches(Regex("[a-f0-9-]{36}\\.enc")))
            val bytes=AtomicFile(File(directory,name)).openRead().use { it.readBytes() }
            val raw=requireNotNull(PhiCrypto.decryptBytes(bytes))
            val job=gson.fromJson(String(raw,Charsets.UTF_8),Job::class.java)
            require(job.schema==1 && job.scope==scope.name && "${job.id}.enc"==name && UUID.fromString(job.id).toString()==job.id)
            require(sha(job.rasterBytes())==job.rasterSHA)
            require(job.key==sha(gson.toJson(listOf(scope.name,job.binding.origin,job.binding.configuredUser,job.binding.actor,job.rowJson,job.imageSHA,job.rasterSHA)).toByteArray(Charsets.UTF_8)))
            if(job.receiptID!=null) {
                val row=job.measurement()
                require(AnnotationReceipt.validate(200,requireNotNull(job.receiptBody),requireNotNull(row.wdCode),requireNotNull(row.imageId),job.woundSHA,job.tissueSHA).first)
                require(JsonParser.parseString(job.receiptBody).asJsonObject.get("annotation_receipt_id").asString==job.receiptID)
            }
            require(job.state!=State.COMPLETE || job.receiptID!=null)
            job
        }.sortedBy { it.createdAt }
    }
    private fun persist(job: Job) {
        check(directory.isDirectory || directory.mkdirs())
        val encrypted=requireNotNull(PhiCrypto.encryptBytes(gson.toJson(job).toByteArray(Charsets.UTF_8)))
        val atomic=AtomicFile(File(directory,"${job.id}.enc"));val stream=atomic.startWrite()
        try { stream.write(encrypted);atomic.finishWrite(stream) }
        catch(error:Exception) {atomic.failWrite(stream);throw error}
    }
    companion object {
        private val locks=ConcurrentHashMap<String,Mutex>()
        private val processors=ConcurrentHashMap<String,Mutex>()
        private val gson=GsonBuilder().registerTypeAdapter(Date::class.java,object:JsonSerializer<Date>,JsonDeserializer<Date> {
            override fun serialize(value:Date,type:java.lang.reflect.Type,context:JsonSerializationContext)=JsonPrimitive(value.time)
            override fun deserialize(value:JsonElement,type:java.lang.reflect.Type,context:JsonDeserializationContext)=Date(value.asLong)
        }).create()
        private fun sha(bytes:ByteArray)=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it)}
        private fun delayFor(attempt:Int)=minOf(3_600_000L,60_000L*(1L shl minOf(attempt,6)))
    }
}
