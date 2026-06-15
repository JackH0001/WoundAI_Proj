using System.Drawing;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Interfaces
{
    /// <summary>
    /// 影像處理模組介面
    /// </summary>
    public interface IProcessingModule : IDisposable
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
        /// 初始化處理模組
        /// </summary>
        /// <param name="settings">處理設定</param>
        /// <returns>初始化是否成功</returns>
        Task<bool> InitializeAsync(ProcessingSettings settings);

        /// <summary>
        /// 處理影像
        /// </summary>
        /// <param name="inputImage">輸入影像</param>
        /// <returns>處理結果</returns>
        Task<ProcessingResult> ProcessImageAsync(ImageData inputImage);

        /// <summary>
        /// 評估影像品質
        /// </summary>
        /// <param name="image">影像資料</param>
        /// <returns>品質評估結果</returns>
        Task<QualityAssessment> AssessQualityAsync(ImageData image);

        /// <summary>
        /// 偵測 ROI (Region of Interest)
        /// </summary>
        /// <param name="image">影像資料</param>
        /// <returns>ROI 區域</returns>
        Task<Rectangle> DetectROIAsync(ImageData image);
    }

    /// <summary>
    /// 處理設定
    /// </summary>
    public class ProcessingSettings
    {
        /// <summary>
        /// 是否啟用白平衡
        /// </summary>
        public bool EnableWhiteBalance { get; set; } = true;

        /// <summary>
        /// 是否啟用 Gamma 校正
        /// </summary>
        public bool EnableGammaCorrection { get; set; } = true;

        /// <summary>
        /// Gamma 值
        /// </summary>
        public double GammaValue { get; set; } = 1.0;

        /// <summary>
        /// 是否啟用雜訊抑制
        /// </summary>
        public bool EnableNoiseReduction { get; set; } = true;

        /// <summary>
        /// 雜訊抑制強度
        /// </summary>
        public double NoiseReductionStrength { get; set; } = 0.5;

        /// <summary>
        /// 是否啟用邊緣增強
        /// </summary>
        public bool EnableEdgeEnhancement { get; set; } = false;

        /// <summary>
        /// 邊緣增強強度
        /// </summary>
        public double EdgeEnhancementStrength { get; set; } = 0.3;

        /// <summary>
        /// 最小品質分數閾值
        /// </summary>
        public double MinQualityScore { get; set; } = 20.0;

        /// <summary>
        /// 最小深度覆蓋率
        /// </summary>
        public double MinDepthCoverage { get; set; } = 0.8;

        /// <summary>
        /// 最小信心度閾值
        /// </summary>
        public double MinConfidence { get; set; } = 0.7;
    }

    /// <summary>
    /// 品質評估結果
    /// </summary>
    public class QualityAssessment
    {
        /// <summary>
        /// 整體品質分數 (0-100)
        /// </summary>
        public double OverallScore { get; set; }

        /// <summary>
        /// 信噪比 (dB)
        /// </summary>
        public double SignalToNoiseRatio { get; set; }

        /// <summary>
        /// 深度覆蓋率 (0-1)
        /// </summary>
        public double DepthCoverage { get; set; }

        /// <summary>
        /// 平均信心度 (0-1)
        /// </summary>
        public double AverageConfidence { get; set; }

        /// <summary>
        /// 運動模糊程度 (0-1)
        /// </summary>
        public double MotionBlur { get; set; }

        /// <summary>
        /// 亮度分數 (0-100)
        /// </summary>
        public double BrightnessScore { get; set; }

        /// <summary>
        /// 對比度分數 (0-100)
        /// </summary>
        public double ContrastScore { get; set; }

        /// <summary>
        /// 是否通過品質檢查
        /// </summary>
        public bool PassesQualityCheck { get; set; }

        /// <summary>
        /// 品質問題列表
        /// </summary>
        public List<string> QualityIssues { get; set; } = new();
    }
} 