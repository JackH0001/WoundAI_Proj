package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.junit4.createComposeRule
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ReviewControlsTest {
    @get:Rule val compose = createComposeRule()
    @Test fun cannotEnterEditorBeforeIdentityLoadsOrDuringSubmission() {
        val loading = mutableStateOf(true)
        val busy = mutableStateOf(false)
        var entries = 0
        compose.setContent { MaterialTheme {
            ReviewEditEntry(loading.value, busy.value, true, true) { entries++ }
        } }
        compose.onNodeWithText("正在驗證登入權限…").assertIsNotEnabled().performClick()
        compose.runOnIdle { assertEquals(0, entries); loading.value = false }
        compose.onNodeWithText("修邊標註（檢視／修正遮罩）").assertIsEnabled().performClick()
        compose.runOnIdle { assertEquals(1, entries); busy.value = true }
        compose.onNodeWithText("修邊標註（檢視／修正遮罩）").assertIsNotEnabled().performClick()
        compose.runOnIdle { assertEquals(1, entries) }
    }
    @Test fun notesStayCollapsedAndCanBeReadWithoutModifyingThem() {
        val notes = "首次量測\n⟳ 第一版\n⟳ 第二版"
        compose.setContent { MaterialTheme { ReviewNotes(notes) } }
        compose.onNodeWithText(notes).assertDoesNotExist()
        compose.onNodeWithText("查看修訂紀錄（2 次）／原始備註").performClick()
        compose.onNodeWithText(notes).assertIsDisplayed()
        compose.onNodeWithText("收合修訂紀錄／原始備註").performClick()
        compose.onNodeWithText(notes).assertDoesNotExist()
    }
    @Test fun historicalPreviewDoesNotInventMissingTissueOrCalibration() {
        val bitmap = Bitmap.createBitmap(32, 32, Bitmap.Config.ARGB_8888)
        compose.setContent { MaterialTheme {
            AnalysisPreview(bitmap, listOf(listOf(2,2), listOf(20,2), listOf(2,20)),
                markerQuad = null, mmPerPx = null, calibMethod = null,
                allowTissueEstimate = false, showCalibration = false)
        } }
        compose.onNodeWithText("傷口輪廓").assertIsDisplayed()
        compose.onNodeWithText("組織").assertIsNotEnabled()
        compose.onNodeWithText("校正框").assertDoesNotExist()
        compose.onNodeWithText("重設檢視").assertIsDisplayed().performClick()
    }
    @Test fun previewTwoFingerZoomAndResetDoNotEditMask() {
        val bitmap = Bitmap.createBitmap(32, 32, Bitmap.Config.ARGB_8888)
        val polygon = listOf(listOf(2,2), listOf(20,2), listOf(2,20))
        compose.setContent { MaterialTheme {
            AnalysisPreview(bitmap, polygon, markerQuad = null, mmPerPx = null,
                calibMethod = null, allowTissueEstimate = false, showCalibration = false)
        } }
        val preview = compose.onNodeWithTag("analysis-preview")
        preview.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "縮放 100%"))
        preview.performTouchInput {
            pinch(Offset(width * .4f, height * .5f), Offset(width * .6f, height * .5f),
                Offset(width * .2f, height * .5f), Offset(width * .8f, height * .5f))
        }
        preview.assert(SemanticsMatcher("zoom increased") {
            it.config[SemanticsProperties.StateDescription] != "縮放 100%"
        })
        compose.onNodeWithText("重設檢視").performClick()
        preview.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "縮放 100%"))
        assertEquals(listOf(listOf(2,2), listOf(20,2), listOf(2,20)), polygon)
    }

}
