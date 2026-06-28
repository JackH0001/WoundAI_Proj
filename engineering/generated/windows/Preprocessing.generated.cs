// // 自動產生自 SSOT preprocessing.json (sha 97552bcb44f3, 2026-06-21)。請勿手改；改 SSOT 後重跑 gen_preprocessing_constants.py。
namespace WoundAI.Generated {
  public enum Norm { Minus1_1, Zero_1, Imagenet }
  public record ModelPreproc(int W,int H,string Layout,string ChannelOrder,Norm Norm,double Threshold);
  public static class Preproc {
    public static readonly ModelPreproc Smp = new(256,256,"NCHW","RGB",Norm.Imagenet,0.3);
    public static readonly ModelPreproc Wsm = new(224,224,"NHWC","BGR",Norm.Zero_1,0.5);
    public static readonly ModelPreproc Fusegnet = new(512,512,"NCHW","RGB",Norm.Imagenet,0.5);
    public static readonly ModelPreproc Deepskin = new(256,256,"NHWC","RGB",Norm.Zero_1,0.5);
    public static readonly ModelPreproc Student = new(256,256,"NCHW","RGB",Norm.Imagenet,0.4);
    public const string RecommendedSticker = "square_20mm";
  }
}
