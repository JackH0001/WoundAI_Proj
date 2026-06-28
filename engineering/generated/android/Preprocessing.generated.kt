// // 自動產生自 SSOT preprocessing.json (sha 97552bcb44f3, 2026-06-21)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
package com.woundmeasurement.app.generated

enum class Norm { MINUS1_1, ZERO_1, IMAGENET }
data class ModelPreproc(val w:Int,val h:Int,val layout:String,val channelOrder:String,val norm:Norm,val threshold:Double)

object Preproc {
  val smp = ModelPreproc(256,256,"NCHW","RGB",Norm.IMAGENET,0.3)
  val wsm = ModelPreproc(224,224,"NHWC","BGR",Norm.ZERO_1,0.5)
  val fusegnet = ModelPreproc(512,512,"NCHW","RGB",Norm.IMAGENET,0.5)
  val deepskin = ModelPreproc(256,256,"NHWC","RGB",Norm.ZERO_1,0.5)
  val student = ModelPreproc(256,256,"NCHW","RGB",Norm.IMAGENET,0.4)
  const val recommendedSticker = "square_20mm"
}
