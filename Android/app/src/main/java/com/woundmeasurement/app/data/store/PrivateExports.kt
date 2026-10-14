package com.woundmeasurement.app.data.store

import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import android.content.Context
import android.graphics.Bitmap
import android.util.AtomicFile
import android.util.Base64
import com.woundmeasurement.app.BuildConfig
import com.woundmeasurement.app.data.crypto.PhiCrypto
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.OutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Encrypted private copies; readable delivery is limited to a user-granted document tree or explicit export. */
class PrivateExports(context: Context, private val scope: MedicalDataScope) {
    private val app = context.applicationContext
    private val prefs = app.getSharedPreferences(scope.settingsName, Context.MODE_PRIVATE)
    internal val directory = File(app.noBackupFilesDir, "administrative_exports/${scope.name.lowercase()}")
    val available get() = BuildConfig.MMH_VALIDATION
    var enabled: Boolean
        get() = available && prefs.getBoolean("administrative_exports.enabled", false)
        set(value) { check(prefs.edit().putBoolean("administrative_exports.enabled", value && available).commit()) }

    var outputFolder: Uri?
        get() = prefs.getString("administrative_exports.folder", null)?.let(Uri::parse)
        private set(value) { check(prefs.edit().putString("administrative_exports.folder", value?.toString()).commit()) }
    fun selectFolder(uri: Uri) {
        require(uri.scheme == "content" && DocumentsContract.isTreeUri(uri))
        app.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        outputFolder = uri
    }
    fun usePrivateFolder() { outputFolder = null }

    /** Only a folder explicitly granted by the system picker. Never silently falls back. */
    fun deliver(name: String) {
        val tree = requireNotNull(outputFolder)
        val resolver = app.contentResolver
        check(resolver.persistedUriPermissions.any { it.uri == tree && it.isReadPermission && it.isWritePermission })
        val verified = read(name)
        val files = deliveryImages(verified)
        val parent = DocumentsContract.buildDocumentUriUsingTree(tree,DocumentsContract.getTreeDocumentId(tree))
        val finalName = deliveryName(verified) + "_" + UUID.randomUUID().toString().take(8)
        val destination = requireNotNull(DocumentsContract.createDocument(resolver,parent,DocumentsContract.Document.MIME_TYPE_DIR,
            "$finalName-partial"))
        try {
            files.forEach { (key,bytes) ->
                val mime = when(key.substringAfterLast('.')) { "jpg" -> "image/jpeg";"png" -> "image/png";else -> "application/json" }
                val file = requireNotNull(DocumentsContract.createDocument(resolver,destination,mime,key))
                requireNotNull(resolver.openOutputStream(file,"wt")).use { it.write(bytes) }
                val restored = requireNotNull(resolver.openInputStream(file)).use { it.readBytes() }
                check(bytes.contentEquals(restored))
            }
            requireNotNull(DocumentsContract.renameDocument(resolver,destination,finalName))
        } catch (e: Exception) {
            runCatching { DocumentsContract.deleteDocument(resolver,destination) }
            throw e
        }
    }

    fun list(): List<String> = directory.listFiles()?.filter { it.isFile && valid(it.name) }
        ?.map { it.name }?.sortedDescending() ?: emptyList()
    private fun valid(name: String) = Regex("(?:[0-9]{13}-[a-f0-9-]{36}|record-[0-9]+-[a-f0-9]{64})\\.enc").matches(name)
    private fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    fun saveBitmap(bitmap: Bitmap, kind: String, metadata: JSONObject = JSONObject(), attachments: Map<String, ByteArray> = emptyMap()): String? {
        if (!enabled) return null
        require(kind in setOf("capture", "overlay"))
        val bytes = ByteArrayOutputStream().use { out ->
            check(bitmap.compress(Bitmap.CompressFormat.JPEG, 100, out)); out.toByteArray()
        }
        // Metadata is about this administrative copy, not a fabricated sensor capture time.
        val info = JSONObject(metadata.toString()).put("schema", "woundai.administrative-copy/1")
            .put("scope", scope.name).put("kind", kind).put("width", bitmap.width).put("height", bitmap.height)
            .put("export_created_at_ms", System.currentTimeMillis())
        require(attachments.keys.none { it == "$kind.jpg" || it == "result.json" })
        return save(attachments + mapOf("$kind.jpg" to bytes, "result.json" to info.toString().toByteArray(Charsets.UTF_8)))
    }

    internal fun save(files: Map<String, ByteArray>, name: String = "${System.currentTimeMillis()}-${UUID.randomUUID()}.enc"): String? {
        if (!enabled) return null
        require(files.isNotEmpty() && files.keys.all { Regex("[a-zA-Z0-9_-]+\\.(jpg|png|json)").matches(it) && it != "sha256.json" })
        val hashes = JSONObject(); val payload = JSONObject()
        files.forEach { (name, bytes) -> hashes.put(name, hash(bytes)); payload.put(name, Base64.encodeToString(bytes, Base64.NO_WRAP)) }
        val envelope = JSONObject().put("scope", scope.name).put("files", payload).put("sha256", hashes)
        val encrypted = requireNotNull(PhiCrypto.encryptBytes(envelope.toString().toByteArray(Charsets.UTF_8)))
        check(directory.isDirectory || directory.mkdirs())
        require(valid(name))
        val atomic = AtomicFile(File(directory, name)); val output = atomic.startWrite()
        try { output.write(encrypted); atomic.finishWrite(output) }
        catch (e: Exception) { atomic.failWrite(output); throw e }
        return name
    }

    internal fun saveRevision(files: Map<String, ByteArray>, recordId: Long, revision: String): String? = synchronized(revisionLock) {
        if (!enabled) return@synchronized null
        require(recordId > 0 && Regex("[a-f0-9]{64}").matches(revision))
        val name = "record-$recordId-$revision.enc"
        if (File(directory,name).exists()) {
            val previous=read(name)
            require(previous.keys == files.keys && files.all { (key,bytes)->bytes.contentEquals(previous[key]) })
            name
        } else save(files,name)
    }
    fun suggestedDeliveryName(name: String): String = deliveryName(read(name))
    companion object {
        private val revisionLock = Any()
        internal fun deliveryImages(files: Map<String, ByteArray>): Map<String, ByteArray> {
            val image = requireNotNull(files["measurement.jpg"] ?: files["image.jpg"] ?: files["capture.jpg"])
            val overlay = requireNotNull(files["overlay.jpg"])
            require(image.isNotEmpty() && overlay.isNotEmpty())
            return mapOf("image.jpg" to image, "overlay.jpg" to overlay)
        }
        internal fun deliveryName(files: Map<String, ByteArray>, at: java.util.Date = java.util.Date()): String {
            val raw = files["delivery.json"]?.let { JSONObject(String(it, Charsets.UTF_8)).optString("medical_record_number", "") } ?: ""
            val safe = raw.trim().replace(Regex("[^A-Za-z0-9_-]"), "_").take(64)
            val label = safe.takeIf { it.any { ch -> ch != '_' && ch != '-' } } ?: "無病歷號"
            val formatter = java.text.SimpleDateFormat("yyyyMMdd_HHmmss_SSS", java.util.Locale.US)
            formatter.timeZone = java.util.TimeZone.getTimeZone("Asia/Taipei")
            return label + "_" + formatter.format(at)
        }
    }

    internal fun read(name: String): Map<String, ByteArray> {
        require(available && valid(name))
        val raw = requireNotNull(PhiCrypto.decryptBytes(File(directory, name).readBytes()))
        val envelope = JSONObject(String(raw, Charsets.UTF_8)); require(envelope.getString("scope") == scope.name)
        val files = envelope.getJSONObject("files"); val hashes = envelope.getJSONObject("sha256")
        val names = files.keys().asSequence().toSet()
        require(names == hashes.keys().asSequence().toSet() && names.isNotEmpty())
        return names.associateWith { key ->
            require(Regex("[a-zA-Z0-9_-]+\\.(jpg|png|json)").matches(key) && key != "sha256.json")
            Base64.decode(files.getString(key), Base64.NO_WRAP).also { require(hash(it) == hashes.getString(key)) }
        }
    }

    /** Caller obtained the destination with ACTION_CREATE_DOCUMENT after an explicit user action. */
    fun export(name: String, destination: OutputStream) {
        val files = deliveryImages(read(name)) // Validate private envelope before selecting the two images.
        ZipOutputStream(destination).use { zip ->
            files.toSortedMap().forEach { (key, bytes) ->
                zip.putNextEntry(ZipEntry(key)); zip.write(bytes); zip.closeEntry()
            }
        }
    }
}
