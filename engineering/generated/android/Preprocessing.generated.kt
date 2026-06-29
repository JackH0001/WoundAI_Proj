// // 自動產生自 SSOT preprocessing.json (sha 77d89346ab42, 2026-06-28)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
package com.woundmeasurement.app.generated

enum class Norm { MINUS1_1, ZERO_1, IMAGENET }
data class ModelPreproc(val w:Int,val h:Int,val layout:String,val channelOrder:String,val norm:Norm,val threshold:Double)

object Preproc {
  val smp = ModelPreproc(256,256,"NCHW","RGB",Norm.IMAGENET,0.3)
  val wsm = ModelPreproc(224,224,"NHWC","BGR",Norm.ZERO_1,0.5)
  val fusegnet = ModelPreproc(512,512,"NCHW","RGB",Norm.IMAGENET,0.5)
  val deepskin = ModelPreproc(256,256,"NHWC","RGB",Norm.ZERO_1,0.5)
  val student = ModelPreproc(256,256,"NCHW","RGB",Norm.IMAGENET,0.4)
  const val recommendedSticker = "square_20mm_v2"
  const val markerMmActive = 12.0
  val pushAreaBands = arrayOf(doubleArrayOf(0.0,0.0), doubleArrayOf(0.3,1.0), doubleArrayOf(0.6,2.0), doubleArrayOf(1.0,3.0), doubleArrayOf(2.0,4.0), doubleArrayOf(3.0,5.0), doubleArrayOf(4.0,6.0), doubleArrayOf(8.0,7.0), doubleArrayOf(12.0,8.0), doubleArrayOf(24.0,9.0))  // >24->10
  val tissueWorstOrder = arrayOf("necrosis","slough","granulation","epithelial")
  val captureFields = arrayOf("rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified")
  val consentRequired = arrayOf("care"); val consentOptional = arrayOf("train")
}
