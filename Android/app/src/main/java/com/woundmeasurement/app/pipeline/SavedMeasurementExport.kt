package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.PrivateExports
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.json.JSONArray
import java.io.ByteArrayOutputStream
import java.security.MessageDigest

/** Generates a revision copy only from a committed row and its saved asset bytes. */
class SavedMeasurementExport(private val runtime: MedicalRuntime, private val exports: PrivateExports) {
    suspend fun save(row: MeasurementEntity): String? = withContext(Dispatchers.IO) {
        if (!exports.enabled) return@withContext null
        check(runtime.isOpen())
        val current = runtime.database.measurementDao().getById(row.id)
        check(current?.copy(annotationSubmitted = row.annotationSubmitted) == row) { "Saved revision changed" }
        check(careAllowed(row)) { "Care consent unavailable" }
        val image = requireNotNull(runtime.images.rawBytes(row.imagePath))
        val raster = row.rasterPath?.let { requireNotNull(runtime.images.rawBytes(it)) }
        val files = build(row, image, raster)
        check(runtime.isOpen() && careAllowed(row))
        val revision = digest(files.toSortedMap().entries.joinToString("\n") { "${it.key}:${digest(it.value)}" }.toByteArray())
        exports.saveRevision(files, row.id, revision)
    }

    suspend fun submitSnapshot(row: MeasurementEntity, image: ByteArray, raster: ByteArray): String? = withContext(Dispatchers.IO) {
        if (!exports.enabled) return@withContext null
        check(runtime.isOpen() && row.doctorVerified && careAllowed(row))
        val files = build(row, image, raster)
        check(runtime.isOpen() && careAllowed(row))
        val revision = digest(files.toSortedMap().entries.joinToString("\n") { "${it.key}:${digest(it.value)}" }.toByteArray())
        val name = requireNotNull(exports.saveRevision(files, row.id, revision))
        try {
            if (exports.outputFolder != null) {
                exports.deliver(name)
                "行政影像、遮罩與結果已輸出至自訂資料夾；研究上傳狀態請另行確認。"
            } else "行政影像、遮罩與結果已保存於 App 私密空間；研究上傳狀態請另行確認。"
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (_: Exception) { "研究資料已入列；自訂資料夾輸出失敗。私密副本保留，可在設定重新選擇資料夾或匯出。" }
    }

    private suspend fun careAllowed(row: MeasurementEntity): Boolean {
        if (row.patientId == null) return row.caseId == null && row.source != "clinical"
        val consent = runtime.repo.activeConsent(row.patientId) ?: return false
        return consent.consentCare && consent.withdrawnAt == null
    }

    companion object {
        private fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        internal fun build(row: MeasurementEntity, imageBytes: ByteArray, rasterBytes: ByteArray?): Map<String, ByteArray> {
            require(row.id > 0)
            val bitmap = requireNotNull(BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size))
            try {
                require(row.imageW == bitmap.width && row.imageH == bitmap.height)
                val raster = if (rasterBytes != null) requireNotNull(EditRasterCodec.decode(rasterBytes,row.rasterMeta,bitmap.width,bitmap.height)) else null
                require((row.rasterPath != null) == (raster != null))
                require(!row.doctorVerified || raster != null)
                val fractions = linkedMapOf("granulation" to row.tissueGranulation, "slough" to row.tissueSlough,
                    "necrosis" to row.tissueNecrosis,"epithelial" to row.tissueEpithelial,"other" to row.tissueOther)
                require(fractions.values.all { it == null || (it.isFinite() && it in 0.0..1.0) })
                val knownFractions = fractions.filterValues { it != null }.mapValues { requireNotNull(it.value) }
                val score = if (knownFractions.size == 5) WoundPipeline.push(row.estimatedArea,knownFractions,row.exudate) else null
                val polys = parsePolygons(row.gtPolygon)
                val info = WoundOverlayRenderer.Info(row.wdCode,row.source,row.estimatedArea,score?.partial,score?.full,
                    row.exudate,knownFractions,row.mmPerPx,null,row.route,row.confidence,row.doctorVerified,
                    raster?.tissueEdited == true,row.timestamp)
                val overlay = requireNotNull(WoundOverlayRenderer.render(bitmap,raster,polys,null,info))
                val overlayBytes = try { ByteArrayOutputStream().use { out ->
                    check(overlay.compress(Bitmap.CompressFormat.JPEG,100,out));out.toByteArray()
                } } finally { overlay.recycle() }
                val metadata = JSONObject().put("schema","woundai.saved-administrative-revision/1")
                    .put("local_record_id",row.id).put("image_id",row.imageId ?: JSONObject.NULL)
                    .put("record_created_at_ms",row.timestamp.time).put("captured_at",JSONObject.NULL)
                    .put("image_w",row.imageW).put("image_h",row.imageH)
                    .put("image_sha256",digest(imageBytes)).put("area_cm2",row.estimatedArea ?: JSONObject.NULL)
                    .put("exudate",row.exudate ?: JSONObject.NULL).put("doctor_verified",row.doctorVerified)
                    .put("push_partial",score?.partial ?: JSONObject.NULL).put("push_full",score?.full ?: JSONObject.NULL)
                    .put("tissue_fractions",JSONObject().apply { fractions.forEach { (key,v)->put(key,v ?: JSONObject.NULL) } })
                    .put("gt_polygons",JSONArray(polys)).put("raster_meta",row.rasterMeta?.let { JSONObject(it) } ?: JSONObject.NULL)
                    .put("raster_sha256",rasterBytes?.let { digest(it) } ?: JSONObject.NULL)
                    .put("source",row.source ?: JSONObject.NULL).put("annotation_provenance",row.annotationProvenance?.let { JSONObject(it) } ?: JSONObject.NULL)
                    .put("calibration_marker_quad",JSONObject.NULL)
                    .put("mask_status",if(raster != null) "saved" else "unavailable")
                return buildMap {
                    put("measurement.jpg",imageBytes);put("overlay.jpg",overlayBytes)
                    put("result.json",metadata.toString().toByteArray(Charsets.UTF_8))
                    if (raster != null) {
                        put("wound.png",Base64.decode(requireNotNull(WoundMaskCodec.encode(raster.mask,raster.mw,raster.mh)),Base64.NO_WRAP))
                        put("tissue.png",Base64.decode(requireNotNull(TissueMaskCodec.encode(raster.tissue,raster.mask,raster.mw,raster.mh)),Base64.NO_WRAP))
                    }
                }
            } finally { bitmap.recycle() }
        }
    }
}
