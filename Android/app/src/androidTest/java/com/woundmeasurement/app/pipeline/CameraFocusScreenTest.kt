package com.woundmeasurement.app.pipeline

import android.Manifest
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Before
import org.junit.Assume.assumeTrue
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Run on emulator only: opens synthetic camera preview, never captures or uploads. */
class CameraFocusScreenTest {
    @get:Rule val compose = createComposeRule()
    @Before fun emulatorPermission() {
        assumeTrue(android.os.Build.HARDWARE in listOf("ranchu", "goldfish"))
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.uiAutomation.grantRuntimePermission(instrumentation.targetContext.packageName, Manifest.permission.CAMERA)
    }
    @Test fun previewTapReportsFocusStatusAndCancelDoesNotCapture() {
        var cancelled = false
        var captured = false
        var error: String? = null
        compose.setContent { MaterialTheme {
            CameraCaptureScreen(onCaptured = { captured = true }, onCancel = { cancelled = true }, onError = { error = it })
        } }
        compose.onNodeWithText(CameraFocusFeedback.Phase.READY.label).assertIsDisplayed()
        compose.waitUntil(15000) {
            compose.onAllNodes(hasText("拍攝") and isEnabled()).fetchSemanticsNodes().isNotEmpty() || error != null
        }
        compose.runOnIdle { assertNull(error) }
        compose.onRoot().performTouchInput { click(center) }
        compose.waitUntil(8000) {
            listOf(CameraFocusFeedback.Phase.CONFIRMED, CameraFocusFeedback.Phase.UNCONFIRMED,
                CameraFocusFeedback.Phase.UNSUPPORTED).any {
                compose.onAllNodesWithText(it.label).fetchSemanticsNodes().isNotEmpty()
            }
        }
        compose.onNodeWithText("取消").performClick()
        compose.runOnIdle { assertTrue(cancelled); assertFalse(captured); assertNull(error) }
    }
}
