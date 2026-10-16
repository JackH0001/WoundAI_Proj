package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test

class TissueEditStatisticsTest {
    private fun raster(edited: Int, count: Int) = EditRaster(
        byteArrayOf(1, 1, 1, 0), byteArrayOf(1, 2, 3, 0), byteArrayOf(1, 1, 1, 1),
        0f, 0f, 2, 2, 1f, null, tissueEditedPx = edited, maskPx = count)
    @Test fun validShrinkAndTissueOnlyCountsAreAcceptedWithoutClipping() {
        for (edited in 0..3) {
            val r = raster(edited, 3)
            assertTrue(r.hasValidTissueEditStatistics)
            assertEquals(edited / 3.0, r.tissueEditRatio, 0.0)
        }
    }
    @Test fun StaleCountersCannotReachTransport() {
        for ((edited, count) in listOf(4 to 3, -1 to 3, 4 to 4)) {
            assertFalse(raster(edited, count).hasValidTissueEditStatistics)
        }
        assertTrue("Do not hide corrupt counters by clamping", raster(4, 3).tissueEditRatio > 1.0)
    }
    @Test fun actualSubmissionRejectsStaleCountsBeforeNetworkOrEncoding() {
        val http = okhttp3.OkHttpClient.Builder().addInterceptor { throw AssertionError("Network must not be reached") }.build()
        val client = BackendClient("https://synthetic.invalid", "", http, bindGoogle = false)
        for (r in listOf(raster(4,3), raster(-1,3))) {
            val result = client.submitAnnotation(code="TEST",gtPolygon=listOf(listOf(0,0),listOf(8,0),listOf(8,8)),
                exudate=0,imageId="fixture",imageW=32,imageH=32,tissueRaster=r,
                consentTrain=true,doctorVerified=true)
            assertFalse(result.first)
            assertTrue(result.second.contains("組織修改比例需重新核對"))
        }
    }
}
