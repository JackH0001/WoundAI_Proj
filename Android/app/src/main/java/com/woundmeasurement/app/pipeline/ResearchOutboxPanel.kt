package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver

@Composable
internal fun ResearchOutboxLifecycle(runtime:MedicalRuntime) {
    val owner=LocalLifecycleOwner.current
    DisposableEffect(runtime,owner) {
        val observer=LifecycleEventObserver { _,event ->
            when(event) {
                Lifecycle.Event.ON_START -> runtime.research.setForeground(true)
                Lifecycle.Event.ON_STOP -> runtime.research.setForeground(false)
                else -> Unit
            }
        }
        owner.lifecycle.addObserver(observer)
        runtime.research.setForeground(owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED))
        onDispose {owner.lifecycle.removeObserver(observer);runtime.research.setForeground(false)}
    }
}

@Composable
internal fun ResearchOutboxPanel(runtime:MedicalRuntime,recordID:Long?=null) {
    val status by runtime.research.status.collectAsState()
    ResearchOutboxStatus(status,recordID) {runtime.research.wake(retryPaused=true)}
}

@Composable
internal fun ResearchOutboxStatus(status:ClinicalResearchController.Status,recordID:Long?=null,onRetry:()->Unit) {
    val items=status.items.filter {recordID==null || it.recordID==recordID}
    if(items.isEmpty() && status.issue==null) return
    val waitingUntil=items.filter{it.state !in setOf(ClinicalAnnotationOutbox.State.COMPLETE,ClinicalAnnotationOutbox.State.CANCELLED)}.maxOfOrNull{it.capacityNotBefore} ?: 0
    val capacityWaiting=waitingUntil>System.currentTimeMillis()
    var expanded by remember {mutableStateOf(false)}
    Column(Modifier.fillMaxWidth().padding(8.dp)) {
        if(status.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        Text(if(status.busy) "正在送出研究標註並核對保存…" else "研究待傳：${status.pending} 筆",style=MaterialTheme.typography.titleSmall)
        if(capacityWaiting) Text("後端容量等待中；資料已保留，到期後自動續傳，無須重複送出。")
        status.issue?.let{Text(it,color=MaterialTheme.colorScheme.error)}
        if(recordID!=null) items.takeLast(1).forEach{Text(it.text)}
        Row {
            TextButton({expanded=!expanded}){Text(if(expanded) "收合狀態" else "查看保存狀態")}
            if(status.pending>0) TextButton(onRetry,enabled=!status.busy && !capacityWaiting){Text("重新核對並重試")}
        }
        if(expanded) {
            items.takeLast(6).forEach{Text("紀錄 ${it.recordID}：${it.text}",style=MaterialTheme.typography.bodySmall)}
            Text("離開此頁仍會處理。系統暫停或關閉 App 時，請再次開啟並登入原帳號以接續；待傳不代表雲端已保存。",style=MaterialTheme.typography.bodySmall)
        }
    }
}
