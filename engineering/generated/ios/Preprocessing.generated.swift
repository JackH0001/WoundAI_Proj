// // 自動產生自 SSOT preprocessing.json (sha 97552bcb44f3, 2026-06-21)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
import Foundation

public enum Norm { case minus1_1, zero_1, imagenet }
public struct ModelPreproc { public let w:Int; public let h:Int; public let layout:String; public let channelOrder:String; public let norm:Norm; public let threshold:Double }
public enum Preproc {
  public static let smp = ModelPreproc(w:256, h:256, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.3)
  public static let wsm = ModelPreproc(w:224, h:224, layout:"NHWC", channelOrder:"BGR", norm:.zero_1, threshold:0.5)
  public static let fusegnet = ModelPreproc(w:512, h:512, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.5)
  public static let deepskin = ModelPreproc(w:256, h:256, layout:"NHWC", channelOrder:"RGB", norm:.zero_1, threshold:0.5)
  public static let student = ModelPreproc(w:256, h:256, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.4)
  public static let recommendedSticker = "square_20mm"
  public static let arucoDict = "DICT_4X4_50"
  public static let sticker_square_20mm = (footprint_mm:20.0, marker_mm:13.0, aruco_id:7)
  public static let sticker_circle_30mm = (footprint_mm:30.0, marker_mm:20.0, aruco_id:8)
}
