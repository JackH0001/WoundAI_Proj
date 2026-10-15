package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test

class CameraFocusFeedbackTest {
    @Test fun onlyPositiveResultConfirmsAdjustableFocus() {
        val f = CameraFocusFeedback()
        assertEquals(CameraFocusFeedback.Phase.READY, f.phase)
        val id = f.begin(true)
        assertEquals(CameraFocusFeedback.Phase.FOCUSING, f.phase)
        assertEquals(CameraFocusFeedback.Phase.CONFIRMED, f.complete(id, true))
    }
    @Test fun failureOrTimeoutCannotShowSuccess() {
        val f = CameraFocusFeedback(); val id = f.begin(true)
        assertEquals(CameraFocusFeedback.Phase.UNCONFIRMED, f.complete(id, false))
        assertEquals(CameraFocusFeedback.Phase.UNCONFIRMED, f.complete(id, true))
    }
    @Test fun fixedOrUnsupportedLensNeverConfirms() {
        val f = CameraFocusFeedback(); val id = f.begin(false)
        assertEquals(CameraFocusFeedback.Phase.UNSUPPORTED, f.complete(id, true))
    }
    @Test fun oldResultCannotConfirmNewTap() {
        val f = CameraFocusFeedback(); val old = f.begin(true); val current = f.begin(true)
        assertEquals(CameraFocusFeedback.Phase.FOCUSING, f.complete(old, true))
        assertEquals(CameraFocusFeedback.Phase.UNCONFIRMED, f.complete(current, false))
    }
    @Test fun leavingOrCaptureInvalidatesCallback() {
        val f = CameraFocusFeedback(); val id = f.begin(true); f.cancel()
        assertEquals(CameraFocusFeedback.Phase.READY, f.complete(id, true))
    }
    @Test fun staleExpiryCannotClearNewRequest() {
        val f = CameraFocusFeedback(); val old = f.begin(true); f.complete(old, true)
        val current = f.begin(true)
        assertEquals(CameraFocusFeedback.Phase.FOCUSING, f.expire(old))
        assertEquals(CameraFocusFeedback.Phase.CONFIRMED, f.complete(current, true))
        assertEquals(CameraFocusFeedback.Phase.READY, f.expire(current))
    }
}
