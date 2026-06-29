// // 自動產生自 SSOT preprocessing.json (sha 77d89346ab42, 2026-06-28)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
namespace WoundAI.Generated {
  public enum Norm { Minus1_1, Zero_1, Imagenet }
  public record ModelPreproc(int W,int H,string Layout,string ChannelOrder,Norm Norm,double Threshold);
  public static class Preproc {
    public static readonly ModelPreproc Smp = new(256,256,"NCHW","RGB",Norm.Imagenet,0.3);
    public static readonly ModelPreproc Wsm = new(224,224,"NHWC","BGR",Norm.Zero_1,0.5);
    public static readonly ModelPreproc Fusegnet = new(512,512,"NCHW","RGB",Norm.Imagenet,0.5);
    public static readonly ModelPreproc Deepskin = new(256,256,"NHWC","RGB",Norm.Zero_1,0.5);
    public static readonly ModelPreproc Student = new(256,256,"NCHW","RGB",Norm.Imagenet,0.4);
    public const string RecommendedSticker = "square_20mm_v2";
    public const double MarkerMmActive = 12.0;
    public static readonly (double,int)[] PushAreaBands = {(0.0,0), (0.3,1), (0.6,2), (1.0,3), (2.0,4), (3.0,5), (4.0,6), (8.0,7), (12.0,8), (24.0,9)};  // >24->10
    public static readonly string[] TissueWorstOrder = {"necrosis","slough","granulation","epithelial"};
    public static readonly string[] CaptureFields = {"rgb","depth_mm","intrinsics_K","sticker_pose","timestamp","deidentified"};
    public static readonly string[] ConsentRequired = {"care"}; public static readonly string[] ConsentOptional = {"train"};
  }
}
