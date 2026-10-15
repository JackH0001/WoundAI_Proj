package com.woundmeasurement.app.pipeline

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class BackendSignInActionsTest {
    @get:Rule val compose = createComposeRule()
    @Test fun savedCredentialsDoNotShowLoggedInOrLogout() {
        compose.setContent { MaterialTheme {
            BackendSignInActions(false,false,null,true,true,true,{},{})
        } }
        compose.onNodeWithText("WoundAI：已儲存帳號，尚未驗證登入").assertIsDisplayed()
        compose.onNodeWithText("登出 WoundAI 帳號").assertDoesNotExist()
        compose.onNodeWithText("儲存並登入").assertIsEnabled()
    }
    @Test fun signInWaitBlocksRepeatedClickAndShowsLogoutOnlyAfterSuccess() {
        val busy=mutableStateOf(false); val signed=mutableStateOf(false); var calls=0
        compose.setContent { MaterialTheme {
            BackendSignInActions(busy.value,signed.value,"醫師",true,true,true,
                { calls++; busy.value=true }, { signed.value=false })
        } }
        compose.onNodeWithText("儲存並登入").performClick()
        compose.onNodeWithText("正在登入…").assertIsNotEnabled().performClick()
        compose.onNodeWithText("登出 WoundAI 帳號").assertDoesNotExist()
        compose.runOnIdle { assertEquals(1,calls); busy.value=false; signed.value=true }
        compose.onNodeWithText("WoundAI：已登入（醫師）").assertIsDisplayed()
        compose.onNodeWithText("登出 WoundAI 帳號").performClick()
        compose.onNodeWithText("登出 WoundAI 帳號").assertDoesNotExist()
    }
    @Test fun healthLoadingDoesNotClaimStillLoggingIn() {
        compose.setContent { MaterialTheme {
            BackendSignInActions(true,true,"醫師",true,true,true,{},{})
        } }
        compose.onNodeWithText("WoundAI：已登入（醫師），正在讀取雲端狀態…").assertIsDisplayed()
        compose.onNodeWithText("正在讀取雲端狀態…").assertIsNotEnabled()
        compose.onNodeWithText("正在登入…").assertDoesNotExist()
    }
    @Test fun expiredGoogleGateCannotShowActiveBackendLogin() {
        compose.setContent { MaterialTheme {
            BackendSignInActions(false,true,"醫師",true,false,true,{},{})
        } }
        compose.onNodeWithText("WoundAI：尚未登入，請先完成 Google 門禁").assertIsDisplayed()
        compose.onNodeWithText("儲存並登入").assertIsNotEnabled()
        compose.onNodeWithText("登出 WoundAI 帳號").assertDoesNotExist()
    }

    @Test fun googleReadyBeforeBusyCallbackClearsStillRestoresBackendOnce() {
        val ready=mutableStateOf(false);val busy=mutableStateOf(true);var calls=0
        compose.setContent {RestoreBackendAfterEntry(ready.value,busy.value,true){calls++}}
        compose.runOnIdle {ready.value=true}
        compose.runOnIdle {assertEquals(0,calls);busy.value=false}
        compose.runOnIdle {assertEquals(1,calls)}
    }
    @Test fun googleBusyClearsBeforeReadyStillRestoresBackendOnce() {
        val ready=mutableStateOf(false);val busy=mutableStateOf(true);var calls=0
        compose.setContent {RestoreBackendAfterEntry(ready.value,busy.value,true){calls++}}
        compose.runOnIdle {busy.value=false}
        compose.runOnIdle {assertEquals(0,calls);ready.value=true}
        compose.runOnIdle {assertEquals(1,calls)}
    }
    @Test fun firstGoogleLoginWithoutSavedPasswordWaitsForExplicitBackendSignIn() {
        val ready=mutableStateOf(false);val busy=mutableStateOf(true);var calls=0
        compose.setContent {RestoreBackendAfterEntry(ready.value,busy.value,false){calls++}}
        compose.runOnIdle {ready.value=true;busy.value=false}
        compose.runOnIdle {assertEquals(0,calls)}
    }
    @Test fun savedPasswordChangeDoesNotLaunchSecondLoginDuringExplicitSave() {
        val saved=mutableStateOf(false);var calls=0
        compose.setContent {RestoreBackendAfterEntry(true,false,saved.value){calls++}}
        compose.runOnIdle {saved.value=true}
        compose.runOnIdle {assertEquals(0,calls)}
    }
}
