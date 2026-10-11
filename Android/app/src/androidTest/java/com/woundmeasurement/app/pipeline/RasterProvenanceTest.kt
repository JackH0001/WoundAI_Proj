package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class RasterProvenanceTest {
    @get:Rule val compose = createComposeRule()
    private fun raster(baseline: Boolean = true): EditRaster {
        val mask = ByteArray(64) { if (it % 8 in 2..5 && it / 8 in 2..5) 1 else 0 }
        return EditRaster(mask, ByteArray(64) { 1 }, mask.copyOf(), 0f, 0f, 8, 8,
            1f, .123, tissueEditedPx = 7, maskPx = 16, canvasW = 8, canvasH = 8,
            autoBaseline = if (baseline) ByteArray(64) { 2 } else null)
    }
    @Test fun codecPreservesBaselineAndLegacySavedCounts() {
        for (hasBaseline in listOf(true, false)) {
            val original = raster(hasBaseline)
            val (png, json) = EditRasterCodec.encode(original)!!
            val restored = EditRasterCodec.decode(png, json, 8, 8)!!
            assertTrue(EditRasterIdentity.sameContent(original, restored))
            assertEquals(7, restored.tissueEditedPx)
            assertEquals(hasBaseline, restored.autoBaseline != null)
            if (hasBaseline) assertArrayEquals(original.autoBaseline, restored.autoBaseline)
            else {
                val legacy = JSONObject(json).apply { remove("tissue_edit_count_known") }.toString()
                assertEquals(7, EditRasterCodec.decode(png, legacy, 8, 8)!!.tissueEditedPx)
            }
        }
    }
    @Test fun rejectsCorruptBaselineAndWrongCanvas() {
        val (png, json) = EditRasterCodec.encode(raster())!!
        assertNull(EditRasterCodec.decode(png, json, 9, 8))
        val bad = JSONObject(json).put("baseline_plane", false).toString()
        assertNull(EditRasterCodec.decode(png, bad, 8, 8))
    }
    @Test fun identityRequiresExactPixelsAndCoordinates() {
        val a = raster(); val b = raster()
        assertTrue(EditRasterIdentity.sameContent(a, b))
        b.tissue[18] = 3
        assertFalse(EditRasterIdentity.sameContent(a, b))
        b.tissue[18] = 1
        assertTrue(EditRasterIdentity.sameContent(a, b))
        b.mask[18] = 0
        assertFalse(EditRasterIdentity.sameContent(a, b))
    }
    @Test fun editorNoChangesReturnsOriginalEvenWithoutHistoricalBaseline() {
        val original = raster(false)
        val polygon = listOf(listOf(2,2), listOf(5,2), listOf(5,5), listOf(2,5))
        var result: EditRaster? = null
        var area: Double? = null
        val bitmap = Bitmap.createBitmap(8,8,Bitmap.Config.ARGB_8888).apply { eraseColor(0xff885544.toInt()) }
        compose.setContent { MaterialTheme {
            WoundEditScreen(bitmap = bitmap, initialPolygon = polygon, originalArea = 16.2,
                tissueFrac = mapOf("granulation" to 1.0), exudate = 0,
                mmPerPx = 123.0, resume = original, onCancel = {},
                onDone = { _, _, _, a, _, r -> result = r; area = a })
        } }
        compose.onNodeWithText("完成修邊").performClick()
        compose.runOnIdle {
            assertSame(original, result)
            assertEquals(7, result!!.tissueEditedPx)
            assertEquals(16.2, area!!, 0.0)
            assertEquals(.123, result!!.cm2PerPx!!, 0.0)
        }
    }
}
