package com.woundmeasurement.app.institution

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class InstitutionEntryProbeTest {
    private val valid = """{"build":{"service":"${InstitutionConfig.SERVICE}","revision":"synthetic"}}"""
    @Test fun busyThenSuccessUsesBoundedBackoff() = runBlocking {
        val codes = ArrayDeque(listOf(429, 503, 200))
        val waits = mutableListOf<Long>(); var notices=0
        InstitutionEntryProbe.verify({ codes.removeFirst() to valid }, { waits.add(it) }, { notices++ })
        assertEquals(listOf(2000L,4000L),waits); assertEquals(2,notices); assertTrue(codes.isEmpty())
    }
    @Test fun exhaustedBusyNeverPasses() = runBlocking {
        var calls=0
        try { InstitutionEntryProbe.verify({ calls++; 503 to valid }, {}) ; fail() }
        catch(e: IOException) { assertTrue(e.message!!.contains("仍忙碌")) }
        assertEquals(3,calls)
    }
    @Test fun authAndRedirectErrorsDoNotRetry() = runBlocking {
        for (code in listOf(401,403,302,500)) {
            var calls=0
            try { InstitutionEntryProbe.verify({ calls++; code to valid }, { fail("unexpected retry") }); fail() }
            catch(_: IOException) { }
            assertEquals(1,calls)
        }
    }
    @Test fun malformedSuccessDoesNotRetry() = runBlocking {
        var calls=0
        try { InstitutionEntryProbe.verify({ calls++; 200 to "<html>login</html>" }, { fail() }); fail() }
        catch(_: IOException) { }
        assertEquals(1,calls)
    }
    @Test fun networkFailureDoesNotRetry() = runBlocking {
        var calls=0
        try { InstitutionEntryProbe.verify({ calls++; throw IOException("synthetic") }, { fail() }); fail() }
        catch(_: IOException) { }
        assertEquals(1,calls)
    }
    @Test fun cancellationDuringBackoffDoesNotRetry() = runBlocking {
        var calls=0
        try { InstitutionEntryProbe.verify({ calls++; 429 to valid }, { throw CancellationException() }); fail() }
        catch(_: CancellationException) { }
        assertEquals(1,calls)
    }
}
