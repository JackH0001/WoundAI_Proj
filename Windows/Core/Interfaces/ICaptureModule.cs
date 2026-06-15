using System.Drawing;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Interfaces
{
    /// <summary>
    /// 影像捕捉模組介面
    /// </summary>
    public interface ICaptureModule : IDisposable
    {
        /// <summary>
        /// 模組名稱
        /// </summary>
        string ModuleName { get; }

        /// <summary>
        /// 是否已初始化
        /// </summary>
        bool IsInitialized { get; }

        /// <summary>
        /// 是否正在捕捉
        /// </summary>
        bool IsCapturing { get; }

        /// <summary>
        /// 支援的解析度列表
        /// </summary>
        IEnumerable<Size> SupportedResolutions { get; }

        /// <summary>
        /// 支援的幀率列表
        /// </summary>
        IEnumerable<int> SupportedFrameRates { get; }

        /// <summary>
        /// 是否支援深度捕捉
        /// </summary>
        bool SupportsDepthCapture { get; }

        /// <summary>
        /// 初始化捕捉模組
        /// </summary>
        /// <param name="settings">捕捉設定</param>
        /// <returns>初始化是否成功</returns>
        Task<bool> InitializeAsync(CaptureSettings settings);

        /// <summary>
        /// 開始捕捉
        /// </summary>
        /// <returns>開始捕捉是否成功</returns>
        Task<bool> StartCaptureAsync();

        /// <summary>
        /// 停止捕捉
        /// </summary>
        /// <returns>停止捕捉是否成功</returns>
        Task<bool> StopCaptureAsync();

        /// <summary>
        /// 捕捉單張影像
        /// </summary>
        /// <returns>捕捉的影像資料</returns>
        Task<ImageData?> CaptureSingleFrameAsync();

        /// <summary>
        /// 影像捕捉事件
        /// </summary>
        event EventHandler<ImageData>? FrameCaptured;

        /// <summary>
        /// 錯誤事件
        /// </summary>
        event EventHandler<string>? ErrorOccurred;
    }

    /// <summary>
    /// 捕捉設定
    /// </summary>
    public class CaptureSettings
    {
        /// <summary>
        /// 解析度
        /// </summary>
        public Size Resolution { get; set; } = new(640, 480);

        /// <summary>
        /// 幀率
        /// </summary>
        public int FrameRate { get; set; } = 30;

        /// <summary>
        /// 是否啟用深度捕捉
        /// </summary>
        public bool EnableDepthCapture { get; set; } = false;

        /// <summary>
        /// 是否啟用信心度捕捉
        /// </summary>
        public bool EnableConfidenceCapture { get; set; } = false;

        /// <summary>
        /// 影像格式
        /// </summary>
        public ImageFormat ImageFormat { get; set; } = ImageFormat.RGB;

        /// <summary>
        /// 自動曝光
        /// </summary>
        public bool AutoExposure { get; set; } = true;

        /// <summary>
        /// 自動白平衡
        /// </summary>
        public bool AutoWhiteBalance { get; set; } = true;
    }

    /// <summary>
    /// 影像格式
    /// </summary>
    public enum ImageFormat
    {
        RGB,
        RGBA,
        Grayscale,
        YUV,
        MJPEG
    }
} 