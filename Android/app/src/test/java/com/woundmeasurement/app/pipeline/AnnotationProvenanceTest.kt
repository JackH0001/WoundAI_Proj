package com.woundmeasurement.app.pipeline
import org.junit.Assert.*
import org.junit.Test

class AnnotationProvenanceTest {
    @Test fun preservesRequestMetadataAcrossReload() {
        val p = AnnotationProvenance("model-v1", mapOf("focus_lapvar" to 193.256, "marker_skew" to 0.42), "app confirm")
        assertEquals(p, AnnotationProvenance.decode(p.encode()))
    }
    @Test fun knownEmptyCaptureIsDifferentFromLegacyMissingData() {
        val p = AnnotationProvenance(null, emptyMap(), "app confirm")
        assertEquals(p, AnnotationProvenance.decode(p.encode()))
        assertNull(AnnotationProvenance.decode(null))
    }
    @Test fun rejectsBrokenFutureAndNonNumericQuality() {
        listOf("garbage", "{}", "{\"schema\":2}", "{\"schema\":1,\"quality\":{\"focus\":\"42\"}}",
            "{\"schema\":1,\"quality\":{\"focus\":1e999}}").forEach { assertNull(AnnotationProvenance.decode(it)) }
    }
    @Test(expected = IllegalArgumentException::class) fun refusesNonFiniteCapturedQuality() {
        AnnotationProvenance(null, mapOf("focus" to Double.NaN), null).encode()
    }
}
