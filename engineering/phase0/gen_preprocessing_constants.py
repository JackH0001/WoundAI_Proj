# -*- coding: utf-8 -*-
"""M1 接線：由 SSOT preprocessing.json 產生「各端前處理常數檔」(Swift/Kotlin/C#)。
目的：根治 iOS/Android/Windows 硬編碼前處理＆跨端不一致——四端編譯期吃同一份 SSOT，
SSOT 一改(例如 wsm→[0,1]BGR)，重跑本檔即同步到所有端。請把產生檔納入各端建置與版控。
用法：python gen_preprocessing_constants.py  → 輸出到 ../generated/{ios,android,windows}/"""
import json, os, hashlib, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
SSOT = os.path.join(HERE, "preprocessing.json")
OUT = os.path.join(HERE, "..", "generated")
P = json.load(open(SSOT, encoding="utf-8"))
SHA = hashlib.sha256(open(SSOT, "rb").read()).hexdigest()[:12]
STAMP = datetime.date.today().isoformat()
M = P["models"]; CAL = P["calibration"]
MARKER_MM = CAL.get("marker_mm_active", 12.0)
# PUSH(NPUAP3.0) 面積子分上限(cm2)→分;組織子分順序(最差優先);與 clinical_rules 一致(SSOT 化)
PUSH_AREA_BANDS = [[0.0,0],[0.3,1],[0.6,2],[1.0,3],[2.0,4],[3.0,5],[4.0,6],[8.0,7],[12.0,8],[24.0,9]]  # >24→10
TISSUE_ORDER = ["necrosis","slough","granulation","epithelial"]  # 子分 4,3,2,1
def _bands(fmt):
    return ", ".join(fmt.format(hi=b[0],sc=b[1]) for b in PUSH_AREA_BANDS)
def norm_enum(n):  # 統一列舉
    return {"[-1,1]": "MINUS1_1", "[0,1]": "ZERO_1", "imagenet": "IMAGENET"}[n]
def banner(c): return f"{c} 自動產生自 SSOT preprocessing.json (sha {SHA}, {STAMP})。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。"
def emit_swift():
    L=["// "+banner("//"),"import Foundation","","public enum Norm { case minus1_1, zero_1, imagenet }",
       "public struct ModelPreproc { public let w:Int; public let h:Int; public let layout:String; public let channelOrder:String; public let norm:Norm; public let threshold:Double }",
       "public enum Preproc {"]
    for k,c in M.items():
        L.append(f'  public static let {k} = ModelPreproc(w:{c["input_size"][0]}, h:{c["input_size"][1]}, layout:"{c["layout"]}", channelOrder:"{c["channel_order"]}", norm:.{ {"MINUS1_1":"minus1_1","ZERO_1":"zero_1","IMAGENET":"imagenet"}[norm_enum(c["normalize"])] }, threshold:{c["threshold"]})')
    L.append(f'  public static let recommendedSticker = "{CAL["recommended"]}"')
    L.append(f'  public static let markerMmActive: Double = {MARKER_MM}')
    L.append('  public static let pushAreaBands: [(Double,Int)] = [' + _bands("({hi},{sc})") + ']  // >24→10')
    L.append('  public static let tissueWorstOrder = ["necrosis","slough","granulation","epithelial"]')
    L.append('  public static let captureFields = ["rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified"]')
    L.append('  public static let consentRequired = ["care"]; public static let consentOptional = ["train"]')
    L.append(f'  public static let arucoDict = "{CAL["aruco_dict"]}"')
    for sk,sv in CAL["stickers"].items():
        L.append(f'  public static let sticker_{sk} = (footprint_mm:{sv.get("footprint_mm",0)}, marker_mm:{sv.get("marker_mm",0)}, aruco_id:{sv.get("aruco_id",7)})')
    L.append("}"); return "\n".join(L)
def emit_kotlin():
    L=["// "+banner("//"),"package com.woundmeasurement.app.generated","","enum class Norm { MINUS1_1, ZERO_1, IMAGENET }",
       "data class ModelPreproc(val w:Int,val h:Int,val layout:String,val channelOrder:String,val norm:Norm,val threshold:Double)","","object Preproc {"]
    for k,c in M.items():
        L.append(f'  val {k} = ModelPreproc({c["input_size"][0]},{c["input_size"][1]},"{c["layout"]}","{c["channel_order"]}",Norm.{norm_enum(c["normalize"])},{c["threshold"]})')
    L.append(f'  const val recommendedSticker = "{CAL["recommended"]}"')
    L.append(f'  const val markerMmActive = {MARKER_MM}')
    L.append('  val pushAreaBands = arrayOf(' + _bands("doubleArrayOf({hi},{sc}.0)") + ')  // >24->10')
    L.append('  val tissueWorstOrder = arrayOf("necrosis","slough","granulation","epithelial")')
    L.append('  val captureFields = arrayOf("rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified")')
    L.append('  val consentRequired = arrayOf("care"); val consentOptional = arrayOf("train")')
    L.append("}"); return "\n".join(L)
def emit_csharp():
    L=["// "+banner("//"),"namespace WoundAI.Generated {","  public enum Norm { Minus1_1, Zero_1, Imagenet }",
       "  public record ModelPreproc(int W,int H,string Layout,string ChannelOrder,Norm Norm,double Threshold);","  public static class Preproc {"]
    cs={"MINUS1_1":"Norm.Minus1_1","ZERO_1":"Norm.Zero_1","IMAGENET":"Norm.Imagenet"}
    for k,c in M.items():
        key=k[0].upper()+k[1:]
        L.append(f'    public static readonly ModelPreproc {key} = new({c["input_size"][0]},{c["input_size"][1]},"{c["layout"]}","{c["channel_order"]}",{cs[norm_enum(c["normalize"])]},{c["threshold"]});')
    L.append(f'    public const string RecommendedSticker = "{CAL["recommended"]}";')
    L.append(f'    public const double MarkerMmActive = {MARKER_MM};')
    L.append('    public static readonly (double,int)[] PushAreaBands = {' + _bands("({hi},{sc})") + '};  // >24->10')
    L.append('    public static readonly string[] TissueWorstOrder = {"necrosis","slough","granulation","epithelial"};')
    L.append('    public static readonly string[] CaptureFields = {"rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified"};')
    L.append('    public static readonly string[] ConsentRequired = {"care"}; public static readonly string[] ConsentOptional = {"train"};')
    L.append("  }\n}"); return "\n".join(L)
files={"ios/Preprocessing.generated.swift":emit_swift(),"android/Preprocessing.generated.kt":emit_kotlin(),"windows/Preprocessing.generated.cs":emit_csharp()}
for rel,txt in files.items():
    fp=os.path.join(OUT,rel); os.makedirs(os.path.dirname(fp),exist_ok=True); open(fp,"w",encoding="utf-8").write(txt+"\n")
    print("產生",rel)
print("SSOT sha",SHA)
