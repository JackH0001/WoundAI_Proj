package com.woundmeasurement.app.pipeline

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ResearchOutboxPanelTest {
    @get:Rule val compose=createComposeRule()
    @Test fun capacityWaitDisablesRepeatedRetryAndExplainsPreservation() {
        var calls=0
        val status=ClinicalResearchController.Status(items=listOf(ClinicalResearchController.Item(
            1,ClinicalAnnotationOutbox.State.RETRY,"等待",capacityNotBefore=System.currentTimeMillis()+300_000)))
        compose.setContent { MaterialTheme {ResearchOutboxStatus(status,onRetry={calls++})} }
        compose.onNodeWithText("後端容量等待中；資料已保留，到期後自動續傳，無須重複送出。").assertIsDisplayed()
        compose.onNodeWithText("重新核對並重試").assertIsNotEnabled().performClick()
        compose.runOnIdle { assertEquals(0,calls) }
    }
    @Test fun pendingBusyAndSavedHaveDistinctFeedbackAndRetryGate() {
        val status=mutableStateOf(ClinicalResearchController.Status(items=listOf(
            ClinicalResearchController.Item(1,ClinicalAnnotationOutbox.State.PENDING,"待傳"))))
        var retries=0
        compose.setContent {MaterialTheme {ResearchOutboxStatus(status.value,1){retries++}}}
        compose.onNodeWithText("待傳").assertIsDisplayed()
        compose.onNodeWithText("重新核對並重試").performClick()
        compose.runOnIdle {assertEquals(1,retries);status.value=status.value.copy(busy=true)}
        compose.onNodeWithText("正在送出研究標註並核對保存…").assertIsDisplayed()
        compose.onNodeWithText("重新核對並重試").assertIsNotEnabled().performClick()
        compose.runOnIdle {assertEquals(1,retries);status.value=ClinicalResearchController.Status(items=listOf(
            ClinicalResearchController.Item(1,ClinicalAnnotationOutbox.State.COMPLETE,"研究標註已確認保存（本筆無深度）")))}
        compose.onNodeWithText("研究標註已確認保存（本筆無深度）").assertIsDisplayed()
        compose.onNodeWithText("重新核對並重試").assertDoesNotExist()
    }
}
