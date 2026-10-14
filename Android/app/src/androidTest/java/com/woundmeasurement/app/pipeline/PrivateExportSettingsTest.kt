package com.woundmeasurement.app.pipeline

import android.content.Context
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.data.store.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class PrivateExportSettingsTest {
    @get:Rule val compose = createComposeRule()
    @Test fun defaultOffTogglesPersistAndReviewRemainsOff() {
        val context=ApplicationProvider.getApplicationContext<Context>()
        val mmh=PrivateExports(context,MedicalDataScope.MMH);val review=PrivateExports(context,MedicalDataScope.SYNTHETIC_REVIEW)
        mmh.enabled=false;review.enabled=false
        var dataScope by mutableStateOf(MedicalDataScope.MMH)
        try {
            compose.setContent { key(dataScope) { MaterialTheme { Column {
                PrivateExportSettings(remember(dataScope){MedicalRuntime(context,dataScope)})
            } } } }
            compose.onNode(isToggleable()).assertIsOff().performClick().assertIsOn()
            compose.runOnIdle {assertTrue(mmh.enabled);assertFalse(review.enabled);dataScope=MedicalDataScope.SYNTHETIC_REVIEW}
            compose.onNode(isToggleable()).assertIsOff()
            compose.runOnIdle {dataScope=MedicalDataScope.MMH}
            compose.onNode(isToggleable()).assertIsOn()
        } finally {mmh.enabled=false;review.enabled=false}
    }
    @Test fun emptyListExplainsNoExportUntilEnabled() {
        val context=ApplicationProvider.getApplicationContext<Context>()
        compose.setContent {MaterialTheme {Column {PrivateExportSettings(MedicalRuntime(context,MedicalDataScope.SYNTHETIC_REVIEW))}}}
        compose.onNodeWithText("選擇副本並匯出…").performClick()
        compose.waitUntil(5000) {compose.onAllNodesWithText("尚無行政副本。開啟選項後，按確認上傳才建立；只存時間軸不輸出。").fetchSemanticsNodes().isNotEmpty()}
        compose.onNodeWithText("尚無行政副本。開啟選項後，按確認上傳才建立；只存時間軸不輸出。").assertIsDisplayed()
    }
}
