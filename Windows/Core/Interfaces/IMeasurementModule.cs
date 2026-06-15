using System.Drawing;
using WoundMeasurement.Core.Models;

// alias 避免「namespace WoundMeasurement」與「class WoundMeasurement」名稱衝突
using WoundMeasurementModel = WoundMeasurement.Core.Models.WoundMeasurement;

namespace WoundMeasurement.Core.Interfaces
{
    /// <summary>
    /// 量測模組介面
    /// </summary>
    public interface IMeasurementModule : IDisposable
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
        /// 初始化量測模組
        /// </summary>
        /// <param name="settings">量測設定</param>
        /// <returns>初始化是否成功</returns>
        Task<bool> InitializeAsync(MeasurementSettings settings);

        /// <summary>
        /// 量測傷口
        /// </summary>
        /// <param name="image">輸入影像</param>
        /// <param name="mask">傷口遮罩</param>
        /// <returns>量測結果</returns>
        Task<WoundMeasurementModel> MeasureWoundAsync(ImageData image, Bitmap mask);

        /// <summary>
        /// 計算傷口面積
        /// </summary>
        /// <param name="mask">傷口遮罩</param>
        /// <param name="pixelSizeMm">像素大小 (毫米)</param>
        /// <returns>面積 (平方毫米)</returns>
        Task<double> CalculateAreaAsync(Bitmap mask, double pixelSizeMm);

        /// <summary>
        /// 計算傷口周長
        /// </summary>
        /// <param name="mask">傷口遮罩</param>
        /// <param name="pixelSizeMm">像素大小 (毫米)</param>
        /// <returns>周長 (毫米)</returns>
        Task<double> CalculatePerimeterAsync(Bitmap mask, double pixelSizeMm);

        /// <summary>
        /// 計算傷口深度
        /// </summary>
        /// <param name="depthMap">深度圖</param>
        /// <param name="mask">傷口遮罩</param>
        /// <returns>深度 (毫米)</returns>
        Task<double> CalculateDepthAsync(float[,] depthMap, Bitmap mask);

        /// <summary>
        /// 計算傷口體積
        /// </summary>
        /// <param name="depthMap">深度圖</param>
        /// <param name="mask">傷口遮罩</param>
        /// <param name="pixelSizeMm">像素大小 (毫米)</param>
        /// <returns>體積 (立方毫米)</returns>
        Task<double> CalculateVolumeAsync(float[,] depthMap, Bitmap mask, double pixelSizeMm);

        /// <summary>
        /// 提取傷口邊界
        /// </summary>
        /// <param name="mask">傷口遮罩</param>
        /// <returns>邊界點陣列</returns>
        Task<Point[]> ExtractBoundaryAsync(Bitmap mask);

        /// <summary>
        /// 校準像素大小
        /// </summary>
        /// <param name="referenceObjectSizeMm">參考物件實際大小 (毫米)</param>
        /// <param name="referenceObjectPixels">參考物件像素大小</param>
        /// <returns>校準是否成功</returns>
        Task<bool> CalibratePixelSizeAsync(double referenceObjectSizeMm, double referenceObjectPixels);
    }

    /// <summary>
    /// 量測設定
    /// </summary>
    public class MeasurementSettings
    {
        /// <summary>
        /// 像素大小 (毫米/像素)
        /// </summary>
        public double PixelSizeMm { get; set; } = 0.1;

        /// <summary>
        /// 是否啟用深度量測
        /// </summary>
        public bool EnableDepthMeasurement { get; set; } = true;

        /// <summary>
        /// 是否啟用體積量測
        /// </summary>
        public bool EnableVolumeMeasurement { get; set; } = true;

        /// <summary>
        /// 邊界平滑度
        /// </summary>
        public double BoundarySmoothing { get; set; } = 0.5;

        /// <summary>
        /// 最小面積閾值 (平方毫米)
        /// </summary>
        public double MinAreaThreshold { get; set; } = 1.0;

        /// <summary>
        /// 最大面積閾值 (平方毫米)
        /// </summary>
        public double MaxAreaThreshold { get; set; } = 10000.0;

        /// <summary>
        /// 量測精度 (小數位數)
        /// </summary>
        public int MeasurementPrecision { get; set; } = 2;

        /// <summary>
        /// 是否啟用自動校準
        /// </summary>
        public bool EnableAutoCalibration { get; set; } = false;

        /// <summary>
        /// 校準參考物件大小 (毫米)
        /// </summary>
        public double CalibrationReferenceSizeMm { get; set; } = 10.0;
    }

    /// <summary>
    /// 量測統計資料
    /// </summary>
    public class MeasurementStatistics
    {
        /// <summary>
        /// 平均面積
        /// </summary>
        public double AverageArea { get; set; }

        /// <summary>
        /// 面積標準差
        /// </summary>
        public double AreaStandardDeviation { get; set; }

        /// <summary>
        /// 平均周長
        /// </summary>
        public double AveragePerimeter { get; set; }

        /// <summary>
        /// 周長標準差
        /// </summary>
        public double PerimeterStandardDeviation { get; set; }

        /// <summary>
        /// 平均深度
        /// </summary>
        public double AverageDepth { get; set; }

        /// <summary>
        /// 深度標準差
        /// </summary>
        public double DepthStandardDeviation { get; set; }

        /// <summary>
        /// 平均體積
        /// </summary>
        public double AverageVolume { get; set; }

        /// <summary>
        /// 體積標準差
        /// </summary>
        public double VolumeStandardDeviation { get; set; }

        /// <summary>
        /// 量測次數
        /// </summary>
        public int MeasurementCount { get; set; }

        /// <summary>
        /// 最後更新時間
        /// </summary>
        public DateTime LastUpdated { get; set; }
    }
} 