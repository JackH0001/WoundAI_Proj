package com.woundmeasurement.app.pipeline
import org.junit.Assert.*
import org.junit.Test
class ResearchCapacityWaitTest {
    private fun delay(header:String?,body:String,date:String?=null)=ResearchCapacityWait.deadline(header,date,body,1_000_000,jitterMs=0)-1_000_000
    @Test fun choosesLargestHint() { assertEquals(180_000,delay("120","{\"retry_after\":180}")) }
    @Test fun usesServerClock() { assertEquals(300_000,delay("Sat, 17 Oct 2026 03:05:00 GMT","{}","Sat, 17 Oct 2026 03:00:00 GMT")) }
    @Test fun rejectsBooleanNegativeAndNonfinite() {
        assertEquals(60_000,delay("-10","{\"retry_after\":true}"))
        assertEquals(60_000,delay("NaN","{\"retry_after\":-1}"))
    }
    @Test fun neverCapsValidServerWaitEarly() {assertEquals(90_000_000,delay("90000","{}"))}
    @Test fun cannotOverflowIntoImmediateRetry() {assertEquals(Long.MAX_VALUE,ResearchCapacityWait.deadline("1e300",null,"{}",1_000_000,jitterMs=0))}
}
