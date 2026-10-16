package com.woundmeasurement.app.pipeline

import android.content.Context
import android.content.ContextWrapper
import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
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
    @Test fun manyCopiesStayPagedAndCollapseWithoutDeletingAnyRevision() {
        val base = ApplicationProvider.getApplicationContext<Context>()
        val prefix = "export-pages-${java.util.UUID.randomUUID()}"
        val ctx = object : ContextWrapper(base) {
            override fun getApplicationContext(): Context = this
            override fun getNoBackupFilesDir() = java.io.File(base.noBackupFilesDir,prefix).apply { mkdirs() }
            override fun getSharedPreferences(name: String, mode: Int) = base.getSharedPreferences("$prefix-$name",mode)
        }
        val store = PrivateExports(ctx,MedicalDataScope.SYNTHETIC_REVIEW)
        try {
            store.enabled = true
            for (i in 1..12) {
                val name = "record-$i-${"a".repeat(64)}.enc"
                requireNotNull(store.save(mapOf("image.jpg" to byteArrayOf(1),"overlay.jpg" to byteArrayOf(2)),name))
                assertTrue(java.io.File(store.directory,name).setLastModified(i * 1000L))
            }
            val original = store.list().associateWith { java.io.File(store.directory,it).readBytes().toList() }
            compose.setContent {
                val registry = requireNotNull(LocalActivityResultRegistryOwner.current)
                CompositionLocalProvider(LocalContext provides ctx, LocalActivityResultRegistryOwner provides registry) { MaterialTheme {
                    Column(Modifier.verticalScroll(rememberScrollState())) { PrivateExportSettings(remember {MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW)}) }
                } }
            }
            compose.onNodeWithText("量測紀錄 12 · 修訂 aaaaaaaa").assertDoesNotExist()
            compose.onNodeWithText("選擇副本並匯出…").performScrollTo().performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithText("第 1 / 3 頁").fetchSemanticsNodes().isNotEmpty() }
            compose.onAllNodes(hasText("量測紀錄",substring=true)).assertCountEquals(5)
            compose.onNodeWithText("量測紀錄 12 · 修訂 aaaaaaaa").assertExists()
            compose.onNodeWithText("量測紀錄 7 · 修訂 aaaaaaaa").assertDoesNotExist()
            compose.onNodeWithText("下一頁").performScrollTo().performClick()
            compose.onNodeWithText("量測紀錄 7 · 修訂 aaaaaaaa").assertExists()
            compose.onAllNodes(hasText("量測紀錄",substring=true)).assertCountEquals(5)
            compose.onNodeWithText("下一頁").performScrollTo().performClick()
            compose.onNodeWithText("第 3 / 3 頁").assertExists()
            compose.onAllNodes(hasText("量測紀錄",substring=true)).assertCountEquals(2)
            compose.onNodeWithText("下一頁").assertIsNotEnabled()
            compose.onNodeWithText("收合副本清單").performScrollTo().performClick()
            compose.onAllNodes(hasText("量測紀錄",substring=true)).assertCountEquals(0)
            compose.onNodeWithText("選擇副本並匯出…").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithText("第 1 / 3 頁").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(original,store.list().associateWith { java.io.File(store.directory,it).readBytes().toList() })
        } finally {
            ctx.noBackupFilesDir.deleteRecursively()
            MedicalDataScope.entries.forEach { base.deleteSharedPreferences("$prefix-${it.settingsName}") }
        }
    }
}
