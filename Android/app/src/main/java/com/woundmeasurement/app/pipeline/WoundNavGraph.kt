package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController

/**
 * 行動端導覽圖(Jetpack Compose Navigation 骨架)。
 * 流程:個案清單→個案詳情→(新增)知情同意→拍攝→量測→修邊→去識別→時間軸。
 * 量測畫面接真正的 [MeasureScreen]/[MeasureViewModel];其餘為占位(待各畫面原生實作)。
 * 同意僅首次新增患者;既有個案「繼續拍攝」由個案詳情直接到拍攝(免重簽)。
 */
object Routes {
    const val CASE_LIST = "caseList"; const val CASE_DETAIL = "caseDetail"
    const val CONSENT = "consent"; const val CAPTURE = "capture"; const val MEASURE = "measure"
    const val REVIEW = "review"; const val DEID = "deid"; const val TIMELINE = "timeline"
}

@Composable
fun WoundNavGraph(
    measureVmProvider: () -> MeasureViewModel,
    nav: NavHostController = rememberNavController()
) {
    NavHost(navController = nav, startDestination = Routes.CASE_LIST) {
        composable(Routes.CASE_LIST) { Placeholder("個案清單", "新增個案 →") { nav.navigate(Routes.CASE_DETAIL) } }
        composable(Routes.CASE_DETAIL) { Placeholder("個案詳情", "新增→知情同意 / 繼續拍攝", { nav.navigate(Routes.CONSENT) }, "繼續拍攝(免重簽)→") { nav.navigate(Routes.CAPTURE) } }
        composable(Routes.CONSENT) { Placeholder("知情同意+電子簽名", "同意並開始拍攝 →") { nav.navigate(Routes.CAPTURE) } }
        composable(Routes.CAPTURE) { Placeholder("拍攝(品質把關)", "拍攝→量測 →") { nav.navigate(Routes.MEASURE) } }
        composable(Routes.MEASURE) {
            MeasureScreen(
                vm = measureVmProvider(),
                onReview = { nav.navigate(Routes.REVIEW) },
                onSaveToTimeline = { nav.navigate(Routes.TIMELINE) }
            )
        }
        composable(Routes.REVIEW) { Placeholder("修邊與標註", "完成→去識別 →") { nav.navigate(Routes.DEID) } }
        composable(Routes.DEID) { Placeholder("去識別化上傳", "上傳→時間軸 →") { nav.navigate(Routes.TIMELINE) } }
        composable(Routes.TIMELINE) { Placeholder("傷口時間軸", "＋新增量測(回拍攝) →") { nav.navigate(Routes.CAPTURE) } }
    }
}

/** 占位畫面(待各原生畫面實作);可含 1–2 個導覽按鈕。 */
@Composable
private fun Placeholder(
    title: String, primaryLabel: String, onPrimary: () -> Unit,
    secondaryLabel: String? = null, onSecondary: (() -> Unit)? = null
) {
    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        Text("（畫面骨架；UI 依 docs/mobile_technical_spec 實作）", style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(8.dp))
        Button(onPrimary, Modifier.fillMaxWidth()) { Text(primaryLabel) }
        if (secondaryLabel != null && onSecondary != null)
            OutlinedButton(onSecondary, Modifier.fillMaxWidth()) { Text(secondaryLabel) }
    }
}
