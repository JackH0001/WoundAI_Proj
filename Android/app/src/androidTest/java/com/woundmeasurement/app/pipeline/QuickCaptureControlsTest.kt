package com.woundmeasurement.app.pipeline

import android.content.Context
import android.graphics.Bitmap
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class QuickCaptureControlsTest {
    @get:Rule val compose=createComposeRule()
    @Test fun onlyPhantomSwitchAndRetakeAfterImageIsLoaded() {
        val ctx=ApplicationProvider.getApplicationContext<Context>()
        val vm=MeasureViewModel(WoundAnalyzer(com.woundmeasurement.app.processing.OnnxSegmentationModule(ctx)))
        val source=mutableStateOf("sample")
        compose.setContent {MaterialTheme {SamplePickerScreen(vm,source=source.value,onSource={source.value=it},allowClinicalSource=false)}}
        compose.onNodeWithText("模擬驗證 / 檢錯").assertDoesNotExist()
        compose.onNodeWithText("樣本來源(必選)").assertDoesNotExist()
        compose.onNodeWithText("模式：端上管線").assertDoesNotExist()
        compose.onNodeWithText("拍照").assertIsEnabled()
        compose.onNode(isToggleable()).performClick()
        compose.runOnIdle {assertEquals("phantom",source.value)}
        compose.runOnIdle {
            MeasureViewModel::class.java.getDeclaredField("lastBitmap").apply{isAccessible=true}
                .set(vm,Bitmap.createBitmap(32,32,Bitmap.Config.ARGB_8888))
            @Suppress("UNCHECKED_CAST")
            val state=MeasureViewModel::class.java.getDeclaredField("_state").apply{isAccessible=true}.get(vm) as MutableStateFlow<MeasureUiState>
            state.value=state.value.copy(loading=true)
        }
        compose.onNodeWithText("重新拍照").assertIsNotEnabled()
        compose.onNode(isToggleable()).assertIsNotEnabled()
    }
}
