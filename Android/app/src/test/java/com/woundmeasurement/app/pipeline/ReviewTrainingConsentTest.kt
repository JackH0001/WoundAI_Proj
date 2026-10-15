package com.woundmeasurement.app.pipeline

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class ReviewTrainingConsentTest {
    @Test fun unlinkedPhantomCanRetryWithoutInventingPatientConsent() = runBlocking {
        assertTrue(ReviewTrainingConsent.allowed("phantom", null) { error("Must not query a nonexistent patient") })
    }
    @Test fun explicitSampleAllowedButLinkedSampleStillRequiresConsent() = runBlocking {
        assertTrue(ReviewTrainingConsent.allowed("sample", null) { error("No patient") })
        assertFalse(ReviewTrainingConsent.allowed("sample", 7) { false })
        assertTrue(ReviewTrainingConsent.allowed("sample", 7) { true })
    }
    @Test fun clinicalRequiresRealCase() = runBlocking {
        assertFalse(ReviewTrainingConsent.allowed("clinical", null) { true })
    }
    @Test fun clinicalRequiresFreshConsent() = runBlocking {
        assertTrue(ReviewTrainingConsent.allowed("clinical", 7) { it == 7L })
        assertFalse(ReviewTrainingConsent.allowed("clinical", 7) { false })
    }
    @Test fun withdrawalBetweenDisplayAndSubmitStopsSubmission() = runBlocking {
        var consent = true
        var reads = 0
        val reader: suspend (Long) -> Boolean = { reads++; consent }
        assertTrue(ReviewTrainingConsent.allowed("clinical", 7, reader))
        consent = false
        assertFalse(ReviewTrainingConsent.allowed("clinical", 7, reader))
        assertEquals(2, reads)
    }
    @Test fun phantomTagCannotBypassLinkedPatientWithdrawal() = runBlocking {
        assertFalse(ReviewTrainingConsent.allowed("phantom", 7) { false })
        assertTrue(ReviewTrainingConsent.allowed("phantom", 7) { true })
    }
    @Test fun unknownSourcesStayClosedEvenWithCase() = runBlocking {
        for (source in listOf("", "PHANTOM", "phantom ", "unknown")) {
            for (id in listOf(null, 7L)) assertFalse(ReviewTrainingConsent.allowed(source, id) { true })
        }
    }
    @Test fun legacyMissingSourceDoesNotBecomePhantom() = runBlocking {
        assertFalse(ReviewTrainingConsent.allowed(null, null) { true })
        assertFalse(ReviewTrainingConsent.allowed(null, 7) { false })
        assertTrue(ReviewTrainingConsent.allowed(null, 7) { true })
    }
    @Test fun externalDoesNotImplyNoPatient() = runBlocking {
        for (source in listOf("external")) {
            assertFalse(ReviewTrainingConsent.allowed(source, null) { true })
            assertFalse(ReviewTrainingConsent.allowed(source, 7) { false })
            assertTrue(ReviewTrainingConsent.allowed(source, 7) { true })
        }
    }
    @Test fun invalidCaseDoesNotGrantPhantomExemption() = runBlocking {
        for (id in listOf(0L, -1L)) assertFalse(ReviewTrainingConsent.allowed("phantom", id) { true })
    }
}
