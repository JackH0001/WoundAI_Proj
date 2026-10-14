package com.woundmeasurement.app.pipeline

import java.util.concurrent.atomic.AtomicBoolean

/** One in-flight submission per screen/view model; release on every terminal path. */
internal class AnnotationUploadGate {
    private val busy = AtomicBoolean(false)
    fun acquire(): Boolean = busy.compareAndSet(false, true)
    fun release() { busy.set(false) }
}
