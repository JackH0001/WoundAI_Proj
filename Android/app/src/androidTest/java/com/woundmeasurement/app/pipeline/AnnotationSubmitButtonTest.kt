package com.woundmeasurement.app.pipeline

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class AnnotationSubmitButtonTest {
    @get:Rule val compose = createComposeRule()
    @Test fun uploadWaitBlocksRepeatTapAndCanRetryAfterFailure() {
        val busy = mutableStateOf(false); var requests = 0
        compose.setContent { MaterialTheme {
            AnnotationSubmitButton(busy.value, true, "送出") { requests++; busy.value = true }
        } }
        compose.onNodeWithText("送出").performClick()
        compose.onNodeWithText("正在確認標註保存…").assertIsNotEnabled().performClick()
        compose.onNodeWithText("正在上傳並等待後端確認，請留在此頁；完成前不要重複送出。").assertIsDisplayed()
        compose.runOnIdle { assertEquals(1, requests); busy.value = false }
        compose.onNodeWithText("送出").assertIsEnabled().performClick()
        compose.runOnIdle { assertEquals(2, requests) }
    }
    @Test fun confirmedRecordCannotBeResent() {
        var requests=0
        compose.setContent { MaterialTheme {
            AnnotationSubmitButton(false,false,"標註已保存，無需補送") { requests++ }
        } }
        compose.onNodeWithText("標註已保存，無需補送").assertIsNotEnabled().performClick()
        compose.runOnIdle { assertEquals(0,requests) }
    }
}
