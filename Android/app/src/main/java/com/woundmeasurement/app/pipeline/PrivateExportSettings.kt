package com.woundmeasurement.app.pipeline

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import com.woundmeasurement.app.data.store.PrivateExports
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun PrivateExportSettings(runtime: MedicalRuntime) {
    val context = LocalContext.current
    val store = remember(runtime) { PrivateExports(context, runtime.scope) }
    if (!store.available) return
    val coroutine = rememberCoroutineScope()
    var enabled by remember(runtime) { mutableStateOf(store.enabled) }
    var expanded by remember(runtime) { mutableStateOf(false) }
    var files by remember(runtime) { mutableStateOf(emptyList<String>()) }
    var page by remember(runtime) { mutableStateOf(0) }
    val pageSize = 5
    var selected by remember(runtime) { mutableStateOf<String?>(null) }
    var busy by remember(runtime) { mutableStateOf(false) }
    var message by remember(runtime) { mutableStateOf<String?>(null) }
    var folder by remember(runtime) { mutableStateOf(store.outputFolder) }
    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            runCatching { store.selectFolder(uri); folder=store.outputFolder }
                .onSuccess { message="已設定輸出資料夾；可選擇既有副本重新輸出，不必再次上傳。" }
                .onFailure { message="無法保留資料夾存取權，請重新選擇。" }
        }
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val name = selected; selected = null
        if (uri != null && name != null) {
            busy = true
            coroutine.launch {
                try {
                    withContext(Dispatchers.IO) {
                        context.contentResolver.openOutputStream(uri, "wt")?.use { store.export(name, it) }
                            ?: error("Cannot open destination")
                    }
                    message = "已匯出所選副本。目的地可能自行同步，請依機構規範保存。"
                } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                catch (_: Exception) { message = "匯出未完成；私密副本仍保留。請檢查目的地，移除可能不完整的匯出檔。" }
                finally { busy = false }
            }
        }
    }
    HorizontalDivider()
    Text("行政影像匯出（本機）", style = MaterialTheme.typography.titleMedium)
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("確認上傳時輸出影像與結果", modifier = Modifier.weight(1f))
        Switch(checked = enabled, enabled = !busy, onCheckedChange = {
            runCatching { store.enabled = it }.onSuccess { enabled = store.enabled }
                .onFailure { message = "設定未能儲存，請重試。" }
        })
    }
    Text("預設關閉。副本加密保存在 App 私密空間並排除系統備份，未選擇自訂資料夾時不自動寫入相簿或同步。關閉不影響時間軸與研究上傳，也不刪除既有副本。", style = MaterialTheme.typography.bodySmall)
    if (enabled) {
        Text(if (folder == null) "輸出位置：App 私密空間" else "輸出位置：${android.provider.DocumentsContract.getTreeDocumentId(folder!!)}")
        OutlinedButton(enabled=!busy,onClick={folderPicker.launch(folder)}) { Text("選擇輸出資料夾…") }
        if (folder != null) TextButton(enabled=!busy,onClick={store.usePrivateFolder();folder=null}) { Text("改用 App 私密空間") }
        Text("按確認上傳時，使用同一份已確認修訂輸出影像、遮罩疊圖與結果。自訂資料夾只輸出 image 與 overlay（含測量數據）兩張影像，以病歷號（無則「無病歷號」）及台北輸出日期時間命名；每次另建資料夾，可能由該服務同步；輸出完成不代表雲端已保存。",style=MaterialTheme.typography.bodySmall)
    }
    OutlinedButton(enabled = !busy && selected == null, onClick = {
        if (expanded) { expanded = false; page = 0 }
        else coroutine.launch {
            files = withContext(Dispatchers.IO) {
                store.list().map { it to java.io.File(store.directory, it).lastModified() }
                    .sortedWith(compareByDescending<Pair<String, Long>> { it.second }.thenBy { it.first })
                    .map { it.first }
            }
            page = 0; expanded = true
        }
    }) { Text(if (expanded) "收合副本清單" else "選擇副本並匯出…") }
    if (expanded) {
        if (files.isEmpty()) Text("尚無行政副本。開啟選項後，按確認上傳才建立；只存時間軸不輸出。")
        Text("以下操作只輸出既有副本，不重新上傳，也不變更研究保存狀態。")
        if (files.isNotEmpty()) Text("共 ${files.size} 份副本 · 最新在前 · 每頁最多 5 份", style = MaterialTheme.typography.bodySmall)
        files.drop(page * pageSize).take(pageSize).forEach { name ->
            TextButton(enabled = !busy && selected == null, onClick = {
                busy = true
                coroutine.launch {
                    try {
                        val suggested = withContext(Dispatchers.IO) { store.suggestedDeliveryName(name) }
                        selected = name; launcher.launch("$suggested.zip")
                    } catch (_: Exception) { message = "無法讀取副本，未開始匯出。" }
                    finally { busy = false }
                }
            }) { Text(if (name.startsWith("record-")) "量測紀錄 ${name.split('-')[1]} · 修訂 ${name.split('-')[2].take(8)}"
                else "拍攝副本 " + java.text.SimpleDateFormat("MM/dd HH:mm:ss", java.util.Locale.TAIWAN)
                .format(java.util.Date(name.take(13).toLong())) + " · " + name.substring(14,20)) }
            if (folder != null) OutlinedButton(modifier = Modifier.testTag("retry-export-$name"), enabled = !busy && selected == null, onClick = {
                busy = true
                coroutine.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            check(runtime.isOpen())
                            store.deliver(name)
                        }
                        message = "image 與 overlay 兩張影像已輸出至設定資料夾；未重新上傳，研究保存狀態未變更。"
                    } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                    catch (_: Exception) {
                        message = "本機輸出未完成；原副本仍保留。請重新選擇資料夾後重試，不必再次上傳。"
                    } finally { busy = false }
                }
            }) { Text("輸出此副本至設定資料夾（不上傳）") }
        }
        if (files.size > pageSize) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(enabled = !busy && selected == null && page > 0, onClick = { page-- }) { Text("上一頁") }
            Text("第 ${page + 1} / ${(files.size + pageSize - 1) / pageSize} 頁")
            TextButton(enabled = !busy && selected == null && (page + 1) * pageSize < files.size, onClick = { page++ }) { Text("下一頁") }
        }
    }
    if (busy) { LinearProgressIndicator(Modifier.fillMaxWidth()); Text("正在匯出所選副本…") }
    message?.let { Text(it) }
    Text("匯出由你選擇位置，輸出為可讀取檔案。舊版已寫入相簿的副本不會自動搬移或刪除。", style = MaterialTheme.typography.bodySmall)
}
