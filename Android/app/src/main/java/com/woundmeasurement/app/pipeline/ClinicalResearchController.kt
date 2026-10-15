package com.woundmeasurement.app.pipeline

import android.content.Context
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.MedicalAccountPreferences
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import java.util.concurrent.atomic.AtomicBoolean

/** App/runtime-scoped worker. Screen navigation does not own or cancel submissions.
 * Android may suspend this process; disk jobs resume at the next foreground/login opportunity.
 */
internal class ClinicalResearchController(context: Context, private val runtime: MedicalRuntime) {
    private val contextForExports=context.applicationContext
    private val outbox=ClinicalAnnotationOutbox(context,runtime.scope)
    private val tasks=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    private val wakeups=Channel<Unit>(Channel.CONFLATED)
    private val drainLock=Mutex()
    private val manual=AtomicBoolean(false)
    @Volatile private var foreground=false
    private var pump: Job?=null
    data class Item(val recordID:Long,val state:ClinicalAnnotationOutbox.State,val text:String,val snapshot:MeasurementEntity?=null)
    data class Status(val items:List<Item> = emptyList(),val busy:Boolean=false,val issue:String?=null) {
        fun tracks(row:MeasurementEntity?):Boolean = row != null && items.any {
            it.state != ClinicalAnnotationOutbox.State.CANCELLED &&
                it.snapshot?.copy(annotationSubmitted=false) == row.copy(annotationSubmitted=false)
        }
        val pending get()=items.count { it.state !in setOf(ClinicalAnnotationOutbox.State.COMPLETE,ClinicalAnnotationOutbox.State.CANCELLED) }
    }
    private val mutable=MutableStateFlow(Status())
    val status=mutable.asStateFlow()
    fun setForeground(value:Boolean) {
        foreground=value
        if(pump==null) pump=tasks.launch {
            while(isActive) {
                if(foreground) drainOnce(manual.getAndSet(false))
                withTimeoutOrNull(30_000){wakeups.receive()}
            }
        }
        wake()
    }
    fun wake(retryPaused:Boolean=false) { if(retryPaused) manual.set(true);wakeups.trySend(Unit) }
    fun close() {foreground=false;tasks.cancel()}

    private fun binding(client:BackendClient)=ClinicalAnnotationOutbox.Binding(runtime.backendUrl(),runtime.backendUser(),
        requireNotNull(client.identity).identity)
    private suspend fun permission(row:MeasurementEntity,expected:ClinicalAnnotationOutbox.Binding,
        client:BackendClient,current:()->Boolean):ClinicalAnnotationOutbox.Permission {
        if(!runtime.isOpen() || !current() || binding(client)!=expected) return ClinicalAnnotationOutbox.Permission.WAIT
        if(client.identity?.can("gt.verify")!=true || client.identity?.can("annotation.submit")!=true) return ClinicalAnnotationOutbox.Permission.WAIT
        val latest=runtime.database.measurementDao().getById(row.id) ?: return ClinicalAnnotationOutbox.Permission.REVOKED
        if(latest.patientId!=row.patientId || latest.caseId!=row.caseId || latest.imageId!=row.imageId || latest.wdCode!=row.wdCode) return ClinicalAnnotationOutbox.Permission.REVOKED
        if(row.wdCode in runtime.accounts.pending(MedicalAccountPreferences.ConsentQueue.WITHDRAWAL)) return ClinicalAnnotationOutbox.Permission.REVOKED
        if(row.wdCode in runtime.accounts.pending(MedicalAccountPreferences.ConsentQueue.RESTORE)) return ClinicalAnnotationOutbox.Permission.WAIT
        if(row.caseId==null) return if(row.patientId==null && ReviewTrainingConsent.isUnlinkedPhantom(row.source,row.caseId)) ClinicalAnnotationOutbox.Permission.ALLOW else ClinicalAnnotationOutbox.Permission.WAIT
        val case=runtime.repo.getCase(row.caseId) ?: return ClinicalAnnotationOutbox.Permission.REVOKED
        if(case.patientId!=row.patientId || case.wdCode!=row.wdCode) return ClinicalAnnotationOutbox.Permission.REVOKED
        val consent=runtime.repo.activeConsent(case.patientId) ?: return ClinicalAnnotationOutbox.Permission.REVOKED
        return if(consent.consentCare && consent.trainEffective) ClinicalAnnotationOutbox.Permission.ALLOW else ClinicalAnnotationOutbox.Permission.REVOKED
    }

    /** Explicit send confirms this displayed revision under the current authorized actor. */
    suspend fun enqueue(row:MeasurementEntity):String=withContext(Dispatchers.IO) {
        val client=runtime.acquire();val expected=binding(client);val current=runtime.operation()
        suspend fun assets():Pair<ByteArray,ByteArray>? {
            val image=runtime.images.rawBytes(row.imagePath) ?: return null
            val raster=runtime.images.rawBytes(row.rasterPath) ?: return null
            return image to raster
        }
        val assets=requireNotNull(assets()) {"已存影像或遮罩不完整"}
        outbox.enqueue(row,expected,assets.first,assets.second,
            current={runtime.database.measurementDao().getById(row.id)},savedAssets={assets()},
            permitted={permission(row,expected,client,current)})
        refresh();wake()
        val exportNote = try {
            SavedMeasurementExport(runtime, com.woundmeasurement.app.data.store.PrivateExports(contextForExports, runtime.scope))
                .submitSnapshot(row, assets.first, assets.second)
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { "研究資料已入列，但行政副本未完成；請保留紀錄並重試輸出。" }
        "研究資料已加密排入待傳；可離開此畫面。請查看研究待傳狀態，尚未確認雲端保存。" + (exportNote?.let { "\n$it" } ?: "")
    }

    internal suspend fun drainOnce(retryPaused:Boolean=false) {
        if(!drainLock.tryLock()) return
        try {
            val jobs=outbox.jobs();refresh(jobs)
            val pending=jobs.filter {it.state !in setOf(ClinicalAnnotationOutbox.State.COMPLETE,ClinicalAnnotationOutbox.State.CANCELLED)}
            if(pending.isEmpty() || !runtime.isOpen()) return
            // Do not repeatedly authenticate an unrelated account or upload an old environment's work.
            val eligible=pending.filter {it.binding.origin==runtime.backendUrl() && it.binding.configuredUser==runtime.backendUser()}
            if(eligible.isEmpty()) {mutable.value=mutable.value.copy(issue="有待傳工作需要原送件帳號登入。");return}
            val due=eligible.filter { (retryPaused || it.nextAttemptAt<=System.currentTimeMillis()) &&
                (it.state!=ClinicalAnnotationOutbox.State.PAUSED || it.issue=="authorization_required" || retryPaused) }
            if(due.isEmpty()) return
            mutable.value=mutable.value.copy(busy=true,issue=null)
            val client=runtime.acquire();val expected=binding(client);val current=runtime.operation()
            if(client.identity?.can("gt.verify")!=true || client.identity?.can("annotation.submit")!=true) {
                mutable.value=mutable.value.copy(issue="目前帳號沒有研究送件權限，請由原送件醫師登入。");return
            }
            for(job in due) {
                if(!current() || !runtime.isOpen()) break
                if(job.binding!=expected) {mutable.value=mutable.value.copy(issue="有待傳工作需要原送件醫師登入。");continue}
                outbox.process(job.id,expected,
                    permitted={permission(it.measurement(),expected,client,current)},
                    upload={
                        val consentNow=permission(it.measurement(),expected,client,current)==ClinicalAnnotationOutbox.Permission.ALLOW
                        check(consentNow)
                        upload(client,it,consentNow)
                    },
                    acknowledge={runtime.database.measurementDao().acknowledgeAnnotationIfUnchanged(it)},
                    retryPaused=retryPaused || job.issue=="authorization_required")
                refresh()
            }
        } catch(cancelled:CancellationException) {throw cancelled}
        catch(_:Exception) {mutable.value=mutable.value.copy(issue="待傳尚未完成：請確認網路、Google／WoundAI 登入及本機資料；紀錄保留。")}
        finally {mutable.value=mutable.value.copy(busy=false);drainLock.unlock()}
    }
    private suspend fun refresh(jobs:List<ClinicalAnnotationOutbox.Job>? = null) {
        val saved=jobs ?: outbox.jobs()
        mutable.value=mutable.value.copy(items=saved.map { job ->
            val text=when(job.state) {
                ClinicalAnnotationOutbox.State.PENDING -> "待傳"
                ClinicalAnnotationOutbox.State.RETRY -> "等待重試，尚未確認保存"
                ClinicalAnnotationOutbox.State.PAUSED -> if(job.issue=="authorization_required") "需確認登入／同意" else "保存回覆未通過，請核對後重試"
                ClinicalAnnotationOutbox.State.RECEIPT_SAVED -> "雲端標註已保存，本機狀態待同步"
                ClinicalAnnotationOutbox.State.COMPLETE -> if(job.localAcknowledged) "研究標註已確認保存（本筆無深度）" else "舊修訂已保存；較新修訂未標記"
                ClinicalAnnotationOutbox.State.CANCELLED -> "已停止：紀錄或同意不再有效"
            }
            Item(job.measurement().id,job.state,text,job.measurement())
        })
    }
    private fun upload(client:BackendClient,job:ClinicalAnnotationOutbox.Job,consentTrain:Boolean):ClinicalAnnotationOutbox.Response {
        val row=job.measurement();val raster=requireNotNull(EditRasterCodec.decode(job.rasterBytes(),row.rasterMeta,requireNotNull(row.imageW),requireNotNull(row.imageH)))
        val provenance=requireNotNull(AnnotationProvenance.decode(row.annotationProvenance))
        val polys=parsePolygons(row.gtPolygon)
        var response:ClinicalAnnotationOutbox.Response?=null
        client.submitAnnotation(requireNotNull(row.wdCode),PolygonJson.largest(polys),row.exudate,
            allPolygons=polys,areaCm2=row.estimatedArea,imageId=row.imageId,imageW=requireNotNull(row.imageW),imageH=requireNotNull(row.imageH),
            mmPerPx=row.mmPerPx,route=row.route,segModel=provenance.segModel,quality=provenance.quality,
            correctionIou=row.correctionIou,careNote=provenance.careNote,source=row.source,
            tissueFrac=mapOf("granulation" to row.tissueGranulation,"slough" to row.tissueSlough,
                "necrosis" to row.tissueNecrosis,"epithelial" to row.tissueEpithelial,"other" to row.tissueOther)
                .mapNotNull{(k,v)->v?.let{k to it}}.toMap(),
            tissueMaskPng=requireNotNull(TissueMaskCodec.encode(raster.tissue,raster.mask,raster.mw,raster.mh)),tissueRaster=raster,
            consentTrain=consentTrain,doctorVerified=row.doctorVerified,onResponse={code,body->response=ClinicalAnnotationOutbox.Response(code,body)})
        return response ?: ClinicalAnnotationOutbox.Response(400,"{}")
    }
}
