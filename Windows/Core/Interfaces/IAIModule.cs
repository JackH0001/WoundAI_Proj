using System.Drawing;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Interfaces
{
    /// <summary>
    /// AI 模組介面
    /// </summary>
    public interface IAIModule : IDisposable
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
        /// 模型版本
        /// </summary>
        string ModelVersion { get; }

        /// <summary>
        /// 支援的輸入解析度
        /// </summary>
        Size SupportedInputSize { get; }

        /// <summary>
        /// 初始化 AI 模組
        /// </summary>
        /// <param name="settings">AI 設定</param>
        /// <returns>初始化是否成功</returns>
        Task<bool> InitializeAsync(AISettings settings);

        /// <summary>
        /// 分類傷口類型
        /// </summary>
        /// <param name="image">輸入影像</param>
        /// <returns>分類結果</returns>
        Task<WoundClassification> ClassifyWoundAsync(ImageData image);

        /// <summary>
        /// 分割傷口區域
        /// </summary>
        /// <param name="image">輸入影像</param>
        /// <returns>分割遮罩</returns>
        Task<Bitmap?> SegmentWoundAsync(ImageData image);

        /// <summary>
        /// 預測傷口癒合進度
        /// </summary>
        /// <param name="image">輸入影像</param>
        /// <returns>癒合進度預測</returns>
        Task<HealingProgress> PredictHealingProgressAsync(ImageData image);

        /// <summary>
        /// 載入模型
        /// </summary>
        /// <param name="modelPath">模型路徑</param>
        /// <returns>載入是否成功</returns>
        Task<bool> LoadModelAsync(string modelPath);

        /// <summary>
        /// 模型載入事件
        /// </summary>
        event EventHandler<string>? ModelLoaded;

        /// <summary>
        /// 推論完成事件
        /// </summary>
        event EventHandler<InferenceResult>? InferenceCompleted;
    }

    /// <summary>
    /// AI 設定
    /// </summary>
    public class AISettings
    {
        /// <summary>
        /// 模型路徑
        /// </summary>
        public string ModelPath { get; set; } = string.Empty;

        /// <summary>
        /// 推論引擎類型
        /// </summary>
        public InferenceEngine Engine { get; set; } = InferenceEngine.ONNX;

        /// <summary>
        /// 是否使用 GPU 加速
        /// </summary>
        public bool UseGPU { get; set; } = false;

        /// <summary>
        /// 批次大小
        /// </summary>
        public int BatchSize { get; set; } = 1;

        /// <summary>
        /// 推論超時時間 (毫秒)
        /// </summary>
        public int InferenceTimeoutMs { get; set; } = 5000;

        /// <summary>
        /// 最小信心度閾值
        /// </summary>
        public double MinConfidenceThreshold { get; set; } = 0.5;

        /// <summary>
        /// 是否啟用後處理
        /// </summary>
        public bool EnablePostProcessing { get; set; } = true;

        /// <summary>
        /// 是否啟用模型快取
        /// </summary>
        public bool EnableModelCaching { get; set; } = true;
    }

    /// <summary>
    /// 推論引擎類型
    /// </summary>
    public enum InferenceEngine
    {
        ONNX,
        TensorFlow,
        PyTorch,
        MLNET,
        OpenVINO
    }

    /// <summary>
    /// 癒合進度預測
    /// </summary>
    public class HealingProgress
    {
        /// <summary>
        /// 癒合階段
        /// </summary>
        public HealingStage Stage { get; set; }

        /// <summary>
        /// 癒合進度百分比 (0-100)
        /// </summary>
        public double ProgressPercentage { get; set; }

        /// <summary>
        /// 預測癒合時間 (天)
        /// </summary>
        public int PredictedHealingDays { get; set; }

        /// <summary>
        /// 信心度
        /// </summary>
        public double Confidence { get; set; }

        /// <summary>
        /// 風險評估
        /// </summary>
        public RiskLevel RiskLevel { get; set; }

        /// <summary>
        /// 建議治療方案
        /// </summary>
        public List<string> TreatmentRecommendations { get; set; } = new();
    }

    /// <summary>
    /// 癒合階段
    /// </summary>
    public enum HealingStage
    {
        Unknown = 0,
        Hemostasis = 1,      // 止血期
        Inflammation = 2,    // 發炎期
        Proliferation = 3,   // 增生期
        Remodeling = 4       // 重塑期
    }

    /// <summary>
    /// 風險等級
    /// </summary>
    public enum RiskLevel
    {
        Low = 0,
        Medium = 1,
        High = 2,
        Critical = 3
    }

    /// <summary>
    /// 推論結果
    /// </summary>
    public class InferenceResult
    {
        /// <summary>
        /// 推論時間 (毫秒)
        /// </summary>
        public long InferenceTimeMs { get; set; }

        /// <summary>
        /// 推論是否成功
        /// </summary>
        public bool IsSuccess { get; set; }

        /// <summary>
        /// 錯誤訊息
        /// </summary>
        public string? ErrorMessage { get; set; }

        /// <summary>
        /// 推論結果資料
        /// </summary>
        public object? Result { get; set; }
    }
} 