package com.woundmeasurement.app.pipeline

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.woundmeasurement.app.data.*

/** Activation waits for versioned persistence and server capability checks. */
@Composable
internal fun MedicalWoundProfileFields(profile:MedicalWoundProfile, onChange:(MedicalWoundProfile)->Unit,
    specialNote:String, onNoteChange:(String)->Unit) {
    val c=MedicalCatalog.shared
    var region by remember { mutableStateOf(c.sites.firstOrNull { it.code==profile.site }?.region.orEmpty()) }
    var expanded by remember { mutableStateOf(false) }
    var showNote by remember { mutableStateOf(false) }
    MedicalCodePicker("身體區域",region,c.regions) { value ->
        region=value
        if(c.sites.firstOrNull{it.code==profile.site}?.region!=value)
            onChange(profile.copy(site="",side="",surface="",digit="not_applicable"))
    }
    MedicalCodePicker("詳細部位",profile.site,c.sites.filter{it.region==region}.map{MedicalOption(it.code,it.label)}) { value ->
        val s=c.sites.first{it.code==value}
        onChange(profile.copy(site=value,side=profile.side.takeIf{it in s.lateralities}.orEmpty(),
            surface=profile.surface.takeIf{it in s.surfaces} ?: s.surfaces.singleOrNull().orEmpty(),
            digit=profile.digit.takeIf{it in s.digits} ?: s.digits.singleOrNull().orEmpty()))
    }
    c.sites.firstOrNull{it.code==profile.site}?.let { s ->
        MedicalCodePicker("左右側",profile.side,c.lateralities.filter{it.code in s.lateralities}){onChange(profile.copy(side=it))}
        if(s.surfaces.size>1) MedicalCodePicker("表面",profile.surface,c.surfaces.filter{it.code in s.surfaces}){onChange(profile.copy(surface=it))}
        if(s.digits.size>1) MedicalCodePicker("指／趾序號",profile.digit,s.digits.map{MedicalOption(it,if(it=="unknown")"尚未判定" else "第 $it 指／趾")}){onChange(profile.copy(digit=it))}
    }
    MedicalCodePicker("主要傷口成因（臨床確認）",profile.etiology,c.etiologies.map{MedicalOption(it.code,it.label)}){onChange(profile.copy(etiology=it))}
    TextButton(onClick={expanded=!expanded}){Text(if(expanded)"收合共存因素" else "共存因素")}
    if(expanded) c.factors.forEach { f ->
        Row { Checkbox(f.code in profile.factors,{checked ->
            onChange(profile.copy(factors=(profile.factors.filter{it!=f.code}+if(checked)listOf(f.code) else emptyList()).sorted()))
        });Text(f.label) }
    }
    val other=profile.site=="other" || profile.etiology=="other"
    if(!other)TextButton(onClick={showNote=!showNote}){Text("特殊案件補充（選填）")}
    if(other || showNote)OutlinedTextField(specialNote,onNoteChange,
        label={Text(if(other)"請補充其他部位／成因" else "特殊情況說明")},modifier=Modifier.fillMaxWidth())
    if(other)Text("其他選項須補充說明；不會用這段文字自動計分。")
    c.guidance(profile.etiology).forEach { i ->
        Text(i.label,style=MaterialTheme.typography.titleMedium)
        Text("可協助整理："+i.automatic);Text("臨床待確認："+i.manual)
        Text("目前僅顯示評估需求，尚未啟用此量表計分。",style=MaterialTheme.typography.bodySmall)
    }
    if(profile.site.isNotEmpty() && profile.etiology.isNotEmpty() && !profile.valid)
        Text("請完成並核對部位、左右側、表面及成因組合。",color=MaterialTheme.colorScheme.error)
}

@Composable
private fun MedicalCodePicker(label:String,value:String,options:List<MedicalOption>,onSelect:(String)->Unit) {
    var open by remember { mutableStateOf(false) }
    OutlinedButton(onClick={open=true},enabled=options.isNotEmpty(),modifier=Modifier.fillMaxWidth()) {
        Text(label+"："+(options.firstOrNull{it.code==value}?.label ?: "請選擇"))
    }
    if(open)AlertDialog(onDismissRequest={open=false},title={Text(label)},text={
        Column(Modifier.heightIn(max=360.dp).verticalScroll(rememberScrollState())) {
            options.forEach { option -> TextButton(onClick={onSelect(option.code);open=false}){Text(option.label)} }
        }
    },confirmButton={TextButton(onClick={open=false}){Text("取消")}})
}
