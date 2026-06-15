using Microsoft.Extensions.Logging;
using OpenCvSharp;
using System.Drawing;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;

// alias 解 System.Drawing.Size vs OpenCvSharp.Size 衝突
// 此檔內 Size 預設指 System.Drawing.Size（與 ICaptureModule 介面對齊）
// 需要 OpenCvSharp.Size 時用 fully qualified
using Size = System.Drawing.Size;

namespace WoundMeasurement.Capture.Modules
{
    /// <summary>
    /// 基於 OpenCV 的影像捕捉模組
    /// </summary>
    public class OpenCVCaptureModule : ICaptureModule
    {
        private readonly ILogger<OpenCVCaptureModule> _logger;
        private VideoCapture? _capture;
        private bool _disposed = false;
        private CaptureSettings? _settings;

        public OpenCVCaptureModule(ILogger<OpenCVCaptureModule> logger)
        {
            _logger = logger;
        }

        public string ModuleName => "OpenCV Capture Module";

        public bool IsInitialized => _capture != null && _capture.IsOpened();

        public bool IsCapturing { get; private set; }

        public IEnumerable<Size> SupportedResolutions => new[]
        {
            new Size(640, 480),
            new Size(1280, 720),
            new Size(1920, 1080),
            new Size(2560, 1440),
            new Size(3840, 2160)
        };

        public IEnumerable<int> SupportedFrameRates => new[]
        {
            15, 24, 25, 30, 60
        };

        public bool SupportsDepthCapture => false; // OpenCV 不支援深度捕捉

        public event EventHandler<ImageData>? FrameCaptured;
        public event EventHandler<string>? ErrorOccurred;

        public async Task<bool> InitializeAsync(CaptureSettings settings)
        {
            try
            {
                _logger.LogInformation("初始化 OpenCV 捕捉模組...");
                _settings = settings;

                // 嘗試開啟預設攝影機
                _capture = new VideoCapture(0);
                if (!_capture.IsOpened())
                {
                    _logger.LogError("無法開啟預設攝影機");
                    ErrorOccurred?.Invoke(this, "無法開啟預設攝影機");
                    return false;
                }

                // 設定解析度
                _capture.Set(VideoCaptureProperties.FrameWidth, settings.Resolution.Width);
                _capture.Set(VideoCaptureProperties.FrameHeight, settings.Resolution.Height);

                // 設定幀率
                _capture.Set(VideoCaptureProperties.Fps, settings.FrameRate);

                // 設定影像格式
                switch (settings.ImageFormat)
                {
                    case ImageFormat.RGB:
                        _capture.Set(VideoCaptureProperties.FourCC, VideoWriter.FourCC('M', 'J', 'P', 'G'));
                        break;
                    case ImageFormat.MJPEG:
                        _capture.Set(VideoCaptureProperties.FourCC, VideoWriter.FourCC('M', 'J', 'P', 'G'));
                        break;
                    case ImageFormat.YUV:
                        _capture.Set(VideoCaptureProperties.FourCC, VideoWriter.FourCC('Y', 'U', 'Y', 'V'));
                        break;
                }

                // 設定自動曝光
                _capture.Set(VideoCaptureProperties.AutoExposure, settings.AutoExposure ? 1 : 0);

                // 設定自動白平衡
                _capture.Set(VideoCaptureProperties.AutoExposure, settings.AutoWhiteBalance ? 1 : 0);

                _logger.LogInformation("OpenCV 捕捉模組初始化成功");
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "OpenCV 捕捉模組初始化失敗");
                ErrorOccurred?.Invoke(this, ex.Message);
                return false;
            }
        }

        public async Task<bool> StartCaptureAsync()
        {
            if (_capture == null || !_capture.IsOpened())
            {
                _logger.LogError("捕捉模組未初始化");
                return false;
            }

            try
            {
                IsCapturing = true;
                _logger.LogInformation("開始即時捕捉");

                // 在背景執行捕捉循環
                _ = Task.Run(CaptureLoop);

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "開始捕捉失敗");
                IsCapturing = false;
                return false;
            }
        }

        public async Task<bool> StopCaptureAsync()
        {
            try
            {
                IsCapturing = false;
                _logger.LogInformation("停止即時捕捉");
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "停止捕捉失敗");
                return false;
            }
        }

        public async Task<ImageData?> CaptureSingleFrameAsync()
        {
            if (_capture == null || !_capture.IsOpened())
            {
                _logger.LogError("捕捉模組未初始化");
                return null;
            }

            try
            {
                using var frame = new Mat();
                if (_capture.Read(frame))
                {
                    if (frame.Empty())
                    {
                        _logger.LogWarning("捕捉到空幀");
                        return null;
                    }

                    // 轉換為 RGB 格式
                    using var rgbFrame = new Mat();
                    Cv2.CvtColor(frame, rgbFrame, ColorConversionCodes.BGR2RGB);

                    // 轉換為 Bitmap
                    var bitmap = OpenCvSharp.Extensions.BitmapConverter.ToBitmap(rgbFrame);

                    var imageData = new ImageData
                    {
                        RgbImage = bitmap,
                        Width = frame.Width,
                        Height = frame.Height,
                        Timestamp = DateTime.Now,
                        QualityScore = CalculateQualityScore(rgbFrame)
                    };

                    _logger.LogDebug("單幀捕捉成功: {Width}x{Height}", frame.Width, frame.Height);
                    return imageData;
                }
                else
                {
                    _logger.LogWarning("無法讀取影像幀");
                    return null;
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "單幀捕捉失敗");
                ErrorOccurred?.Invoke(this, ex.Message);
                return null;
            }
        }

        private async Task CaptureLoop()
        {
            while (IsCapturing && _capture != null && _capture.IsOpened())
            {
                try
                {
                    var imageData = await CaptureSingleFrameAsync();
                    if (imageData != null)
                    {
                        FrameCaptured?.Invoke(this, imageData);
                    }

                    // 根據幀率控制捕捉頻率
                    var frameRate = _settings?.FrameRate ?? 30;
                    var delayMs = 1000 / frameRate;
                    await Task.Delay(delayMs);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "捕捉循環中發生錯誤");
                    ErrorOccurred?.Invoke(this, ex.Message);
                    await Task.Delay(1000); // 錯誤時延遲 1 秒
                }
            }
        }

        private double CalculateQualityScore(Mat frame)
        {
            try
            {
                // 進階影像品質評估系統
                var qualityMetrics = AnalyzeAdvancedQuality(frame);
                
                // 加權計算綜合分數
                var weights = new Dictionary<string, double>
                {
                    ["brightness"] = 0.15,
                    ["contrast"] = 0.20,
                    ["sharpness"] = 0.25,
                    ["noise"] = 0.15,
                    ["colorBalance"] = 0.10,
                    ["exposure"] = 0.15
                };

                var totalScore = 0.0;
                foreach (var metric in qualityMetrics)
                {
                    if (weights.ContainsKey(metric.Key))
                    {
                        totalScore += metric.Value * weights[metric.Key];
                    }
                }

                var finalScore = Math.Round(totalScore, 2);
                
                _logger.LogDebug("品質分析完成: 亮度={Brightness:F2}, 對比度={Contrast:F2}, 清晰度={Sharpness:F2}, " +
                               "雜訊={Noise:F2}, 色彩平衡={ColorBalance:F2}, 曝光={Exposure:F2}, 總分={Score:F2}",
                               qualityMetrics["brightness"], qualityMetrics["contrast"], qualityMetrics["sharpness"],
                               qualityMetrics["noise"], qualityMetrics["colorBalance"], qualityMetrics["exposure"], finalScore);

                return finalScore;
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "計算品質分數失敗");
                return 50.0; // 預設中等品質
            }
        }
        
        private Dictionary<string, double> AnalyzeAdvancedQuality(Mat frame)
        {
            var metrics = new Dictionary<string, double>();

            // 轉換為灰度圖用於部分分析
            using var gray = new Mat();
            Cv2.CvtColor(frame, gray, ColorConversionCodes.RGB2GRAY);

            // 1. 亮度分析 (改進版)
            metrics["brightness"] = AnalyzeBrightness(gray);

            // 2. 對比度分析 (改進版)
            metrics["contrast"] = AnalyzeContrast(gray);

            // 3. 清晰度分析 (多種方法結合)
            metrics["sharpness"] = AnalyzeSharpness(gray);

            // 4. 雜訊分析 (新增)
            metrics["noise"] = AnalyzeNoise(gray);

            // 5. 色彩平衡分析 (新增)
            metrics["colorBalance"] = AnalyzeColorBalance(frame);

            // 6. 曝光分析 (新增)
            metrics["exposure"] = AnalyzeExposure(gray);

            return metrics;
        }

        private double AnalyzeBrightness(Mat gray)
        {
            var mean = Cv2.Mean(gray).Val0;
            
            // 理想亮度範圍 100-180 (0-255)
            var idealRange = new[] { 100.0, 180.0 };
            
            if (mean < idealRange[0])
                return Math.Max(0, mean / idealRange[0] * 80); // 過暗懲罰
            else if (mean > idealRange[1])
                return Math.Max(0, 100 - ((mean - idealRange[1]) / (255 - idealRange[1]) * 40)); // 過亮懲罰
            else
                return 100; // 理想範圍
        }

        private double AnalyzeContrast(Mat gray)
        {
            // OpenCvSharp 4.8 的 MeanStdDev 用 out 不是 ref；
            // 接受 Scalar 或 Mat 簽章，這裡用 Scalar 讓 lite 介面成立
            Cv2.MeanStdDev(gray, out Scalar mean, out Scalar stdDev);

            var contrast = stdDev.Val0;
            
            // 理想對比度範圍 30-80
            var idealRange = new[] { 30.0, 80.0 };
            
            if (contrast < idealRange[0])
                return Math.Max(0, contrast / idealRange[0] * 70); // 對比度不足
            else if (contrast > idealRange[1])
                return Math.Max(0, 100 - ((contrast - idealRange[1]) / (128 - idealRange[1]) * 30)); // 對比度過高
            else
                return 100; // 理想範圍
        }

        private double AnalyzeSharpness(Mat gray)
        {
            // 方法1: Laplacian變異數
            using var laplacian = new Mat();
            Cv2.Laplacian(gray, laplacian, MatType.CV_64F);
            var laplacianVar = CalculateVariance(laplacian);

            // 方法2: Sobel梯度
            using var sobelX = new Mat();
            using var sobelY = new Mat();
            Cv2.Sobel(gray, sobelX, MatType.CV_64F, 1, 0, ksize: 3);
            Cv2.Sobel(gray, sobelY, MatType.CV_64F, 0, 1, ksize: 3);
            
            using var magnitude = new Mat();
            Cv2.Magnitude(sobelX, sobelY, magnitude);
            var sobelMean = Cv2.Mean(magnitude).Val0;

            // 方法3: Brenner梯度
            var brennerScore = CalculateBrennerGradient(gray);

            // 綜合評分
            var normalizedLaplacian = Math.Min(100, laplacianVar / 100.0 * 100);
            var normalizedSobel = Math.Min(100, sobelMean / 50.0 * 100);
            var normalizedBrenner = Math.Min(100, brennerScore / 1000.0 * 100);

            return (normalizedLaplacian + normalizedSobel + normalizedBrenner) / 3.0;
        }

        private double AnalyzeNoise(Mat gray)
        {
            // 使用局部標準差方法估計雜訊
            using var blurred = new Mat();
            Cv2.GaussianBlur(gray, blurred, new OpenCvSharp.Size(5, 5), 0);
            
            using var diff = new Mat();
            Cv2.Absdiff(gray, blurred, diff);
            
            var noiseMean = Cv2.Mean(diff).Val0;
            
            // 雜訊越少分數越高
            return Math.Max(0, 100 - (noiseMean / 20.0 * 100));
        }

        private double AnalyzeColorBalance(Mat frame)
        {
            // 分析RGB通道平衡
            var channels = new Mat[3];
            Cv2.Split(frame, out channels);

            var means = new double[3];
            for (int i = 0; i < 3; i++)
            {
                means[i] = Cv2.Mean(channels[i]).Val0;
                channels[i].Dispose();
            }

            // 計算通道間的偏差
            var avgMean = means.Average();
            var colorDeviations = means.Select(m => Math.Abs(m - avgMean) / avgMean).ToArray();
            var maxDeviation = colorDeviations.Max();

            // 偏差越小色彩平衡越好
            return Math.Max(0, 100 - (maxDeviation * 400));
        }

        private double AnalyzeExposure(Mat gray)
        {
            // 計算直方圖
            var histogram = new Mat();
            Cv2.CalcHist(new[] { gray }, new[] { 0 }, null, histogram, 1, new[] { 256 }, new[] { new[] { 0f, 256f } });

            // 分析過曝和欠曝區域
            var totalPixels = gray.Rows * gray.Cols;
            var underexposed = 0f;
            var overexposed = 0f;

            // 統計過暗像素 (0-30)
            for (int i = 0; i < 30; i++)
            {
                underexposed += histogram.At<float>(i);
            }

            // 統計過亮像素 (225-255)
            for (int i = 225; i < 256; i++)
            {
                overexposed += histogram.At<float>(i);
            }

            var underexposedRatio = underexposed / totalPixels;
            var overexposedRatio = overexposed / totalPixels;

            // 理想情況下過曝和欠曝比例都應該很低
            var exposureScore = 100 - (underexposedRatio * 200 + overexposedRatio * 200);
            
            histogram.Dispose();
            return Math.Max(0, exposureScore);
        }

        private double CalculateVariance(Mat mat)
        {
            Cv2.MeanStdDev(mat, out Scalar mean, out Scalar stdDev);
            return stdDev.Val0 * stdDev.Val0; // 變異數 = 標準差的平方
        }

        private double CalculateBrennerGradient(Mat gray)
        {
            double brennerSum = 0;
            var rows = gray.Rows;
            var cols = gray.Cols;

            unsafe
            {
                var data = (byte*)gray.DataPointer;
                
                for (int i = 0; i < rows; i++)
                {
                    for (int j = 0; j < cols - 2; j++)
                    {
                        var idx = i * cols + j;
                        var diff = Math.Abs(data[idx + 2] - data[idx]);
                        brennerSum += diff * diff;
                    }
                }
            }

            return brennerSum / (rows * (cols - 2));
        }

        public void Dispose()
        {
            if (!_disposed)
            {
                IsCapturing = false;
                _capture?.Dispose();
                _capture = null;
                _disposed = true;
            }
        }
    }
} 