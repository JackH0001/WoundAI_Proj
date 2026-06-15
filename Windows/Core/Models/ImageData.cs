using System.Drawing;

namespace WoundMeasurement.Core.Models
{
    /// <summary>
    /// 影像資料模型
    /// </summary>
    public class ImageData
    {
        /// <summary>
        /// RGB 影像
        /// </summary>
        public Bitmap? RgbImage { get; set; }

        /// <summary>
        /// 深度影像 (可選)
        /// </summary>
        public float[,]? DepthMap { get; set; }

        /// <summary>
        /// 深度影像陣列格式 (可選)
        /// </summary>
        public float[]? DepthData { get; set; }

        /// <summary>
        /// 信心度影像 (可選)
        /// </summary>
        public float[,]? ConfidenceMap { get; set; }

        /// <summary>
        /// 影像寬度
        /// </summary>
        public int Width { get; set; }

        /// <summary>
        /// 影像高度
        /// </summary>
        public int Height { get; set; }

        /// <summary>
        /// 時間戳記
        /// </summary>
        public DateTime Timestamp { get; set; }

        /// <summary>
        /// 影像品質分數 (0-100)
        /// </summary>
        public double QualityScore { get; set; }

        /// <summary>
        /// 深度品質指標 (可選)
        /// </summary>
        public DepthQualityMetrics? DepthMetrics { get; set; }

        /// <summary>
        /// ROI 檢測結果 (可選)
        /// </summary>
        public ROIDetectionResult? ROIResult { get; set; }

        /// <summary>
        /// 多尺度影像金字塔 (可選)
        /// </summary>
        public List<Bitmap>? MultiScaleImages { get; set; }

        /// <summary>
        /// 額外的元數據
        /// </summary>
        public Dictionary<string, object> Metadata { get; set; } = new();

        /// <summary>
        /// 是否有深度資料
        /// </summary>
        public bool HasDepthData => DepthMap != null || (DepthData != null && DepthData.Length > 0);

        /// <summary>
        /// 是否有信心度資料
        /// </summary>
        public bool HasConfidenceData => ConfidenceMap != null;

        /// <summary>
        /// 是否為高品質影像
        /// </summary>
        public bool IsHighQuality => QualityScore >= 80.0;
    }

    /// <summary>
    /// 傷口分類結果
    /// </summary>
    public class WoundClassification
    {
        /// <summary>
        /// 急性傷口機率
        /// </summary>
        public double AcuteProbability { get; set; }

        /// <summary>
        /// 慢性傷口機率
        /// </summary>
        public double ChronicProbability { get; set; }

        /// <summary>
        /// 預測的傷口類型
        /// </summary>
        public WoundType PredictedType { get; set; }

        /// <summary>
        /// 信心度
        /// </summary>
        public double Confidence { get; set; }
    }

    /// <summary>
    /// 傷口類型列舉
    /// </summary>
    public enum WoundType
    {
        Unknown = 0,
        Acute = 1,
        Chronic = 2,
        PressureUlcer = 3,
        DiabeticUlcer = 4,
        SurgicalWound = 5,
        Burn = 6
    }

    /// <summary>
    /// 傷口量測結果
    /// </summary>
    public class WoundMeasurement
    {
        /// <summary>
        /// 傷口面積 (平方毫米)
        /// </summary>
        public double Area { get; set; }

        /// <summary>
        /// 傷口周長 (毫米)
        /// </summary>
        public double Perimeter { get; set; }

        /// <summary>
        /// 傷口深度 (毫米)
        /// </summary>
        public double Depth { get; set; }

        /// <summary>
        /// 傷口體積 (立方毫米)
        /// </summary>
        public double Volume { get; set; }

        /// <summary>
        /// 傷口邊界點
        /// </summary>
        public Point[] BoundaryPoints { get; set; } = Array.Empty<Point>();

        /// <summary>
        /// 傷口遮罩影像
        /// </summary>
        public Bitmap? MaskImage { get; set; }

        /// <summary>
        /// 量測時間戳記
        /// </summary>
        public DateTime Timestamp { get; set; }
    }

    /// <summary>
    /// 處理結果
    /// </summary>
    public class ProcessingResult
    {
        /// <summary>
        /// 原始影像
        /// </summary>
        public ImageData OriginalImage { get; set; } = new();

        /// <summary>
        /// 處理後的影像
        /// </summary>
        public ImageData ProcessedImage { get; set; } = new();

        /// <summary>
        /// 傷口分類結果
        /// </summary>
        public WoundClassification? Classification { get; set; }

        /// <summary>
        /// 傷口量測結果
        /// </summary>
        public WoundMeasurement? Measurement { get; set; }

        /// <summary>
        /// 處理是否成功
        /// </summary>
        public bool IsSuccess { get; set; }

        /// <summary>
        /// 錯誤訊息
        /// </summary>
        public string? ErrorMessage { get; set; }

        /// <summary>
        /// 處理時間 (毫秒)
        /// </summary>
        public long ProcessingTimeMs { get; set; }
    }

    /// <summary>
    /// ROI 檢測結果
    /// </summary>
    public class ROIDetectionResult
    {
        public Rectangle BoundingBox { get; set; }
        public double Confidence { get; set; }
        public string DetectionMethod { get; set; } = string.Empty;
        public Dictionary<string, double> Features { get; set; } = new();
    }

    /// <summary>
    /// 深度品質指標
    /// </summary>
    public class DepthQualityMetrics
    {
        public double Coverage { get; set; }
        public double Consistency { get; set; }
        public double Accuracy { get; set; }
        public double NoiseLevel { get; set; }
        public double OverallQuality { get; set; }
        public int ValidPixelCount { get; set; }
        public int TotalPixelCount { get; set; }
    }

    /// <summary>
    /// 深度統計資訊
    /// </summary>
    public class DepthStatistics
    {
        public float MinDepth { get; set; }
        public float MaxDepth { get; set; }
        public double MeanDepth { get; set; }
        public float MedianDepth { get; set; }
        public int ValidPixelCount { get; set; }
        public int TotalPixelCount { get; set; }
        public double Coverage { get; set; }
    }
} 