package com.woundmeasurement.app.pipeline

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import com.woundmeasurement.app.BuildConfig
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class BackendSettingsSmokeTest {
    @get:Rule val compose=createComposeRule()
    @Test fun isolatedReviewSettingsDoesNotRequireGoogle() {
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val runtime = MedicalRuntime(context, com.woundmeasurement.app.data.store.MedicalDataScope.SYNTHETIC_REVIEW)
        compose.setContent { androidx.compose.runtime.CompositionLocalProvider(LocalMedicalRuntime provides runtime) {
            MaterialTheme { BackendSettingsScreen {} }
        } }
        compose.onNodeWithText("合成示範環境").assertIsDisplayed()
        compose.onNodeWithText("Sign in with Google").assertDoesNotExist()
        compose.onNodeWithText("儲存並登入").performScrollTo().assertIsEnabled()
        compose.onNodeWithText("登出 WoundAI 帳號").assertDoesNotExist()
    }
    @Test fun opensSettingsAndReturnsWithoutCredentials() {
        var returned=false
        compose.setContent { MaterialTheme { BackendSettingsScreen { returned=true } } }
        compose.onNodeWithText("後端連線設定").assertIsDisplayed()
        if (BuildConfig.MMH_VALIDATION) {
            compose.onNodeWithText("Sign in with Google").assertIsDisplayed()
            compose.onNodeWithText("儲存並登入").performScrollTo().assertIsNotEnabled()
        }
        compose.onNodeWithText("返回").performScrollTo().performClick()
        compose.runOnIdle { assertTrue(returned) }
    }
    @Test fun opensAccountFieldWithoutCrash() {
        compose.setContent { MaterialTheme { BackendSettingsScreen {} } }
        compose.onNodeWithText("帳號",useUnmergedTree=true).performScrollTo().assertExists()
        if (!BuildConfig.MMH_VALIDATION) compose.onNode(hasSetTextAction() and hasText("帳號")).performTextInput("synthetic-local-only")
        compose.onNodeWithText("收起鍵盤").performScrollTo().performClick()
    }
}
