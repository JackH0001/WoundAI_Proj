package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

class AnnotationUploadGateTest {
    @Test fun repeatedTapBlockedUntilCompletion() {
        val gate=AnnotationUploadGate()
        assertTrue(gate.acquire()); repeat(30) { assertFalse(gate.acquire()) }
        gate.release(); assertTrue(gate.acquire())
    }
    @Test fun simultaneousAttemptsHaveOneWinner() {
        val gate=AnnotationUploadGate(); val start=CountDownLatch(1); val wins=AtomicInteger()
        val threads=(1..32).map { thread { start.await(); if(gate.acquire()) wins.incrementAndGet() } }
        start.countDown(); threads.forEach { it.join() }; assertEquals(1,wins.get())
    }
    @Test fun failureAllowsRetryAfterFinally() {
        val gate=AnnotationUploadGate()
        try { assertTrue(gate.acquire()); error("simulated timeout") }
        catch (_: IllegalStateException) {} finally { gate.release() }
        assertTrue(gate.acquire())
    }
}
