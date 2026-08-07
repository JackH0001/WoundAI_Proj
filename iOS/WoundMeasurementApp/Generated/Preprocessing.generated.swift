// // 自動產生自 SSOT preprocessing.json (sha 77d89346ab42, 2026-06-28)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
import Foundation

public enum Norm { case minus1_1, zero_1, imagenet }
public struct ModelPreproc { public let w:Int; public let h:Int; public let layout:String; public let channelOrder:String; public let norm:Norm; public let threshold:Double }
public enum Preproc {
  public static let smp = ModelPreproc(w:256, h:256, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.3)
  public static let wsm = ModelPreproc(w:224, h:224, layout:"NHWC", channelOrder:"BGR", norm:.zero_1, threshold:0.5)
  public static let fusegnet = ModelPreproc(w:512, h:512, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.5)
  public static let deepskin = ModelPreproc(w:256, h:256, layout:"NHWC", channelOrder:"RGB", norm:.zero_1, threshold:0.5)
  public static let student = ModelPreproc(w:256, h:256, layout:"NCHW", channelOrder:"RGB", norm:.imagenet, threshold:0.4)
  public static let recommendedSticker = "square_20mm_v2"
  public static let markerMmActive: Double = 12.0
  public static let pushAreaBands: [(Double,Int)] = [(0.0,0), (0.3,1), (0.6,2), (1.0,3), (2.0,4), (3.0,5), (4.0,6), (8.0,7), (12.0,8), (24.0,9)]  // >24→10
  public static let tissueWorstOrder = ["necrosis","slough","granulation","epithelial"]
  public static let captureFields = ["rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified"]
  public static let consentRequired = ["care"]; public static let consentOptional = ["train"]
  public static let arucoDict = "DICT_4X4_50"
  public static let sticker_square_20mm = (footprint_mm:20.0, marker_mm:13.0, aruco_id:7)
  public static let sticker_circle_30mm = (footprint_mm:30.0, marker_mm:20.0, aruco_id:8)
  public static let sticker_clean_aruco_15mm = (footprint_mm:0, marker_mm:15.0, aruco_id:7)
  public static let sticker_square_20mm_v2 = (footprint_mm:20.0, marker_mm:12.0, aruco_id:7)
  public static let sticker_circle_30mm_v2 = (footprint_mm:30.0, marker_mm:15.0, aruco_id:7)
}
