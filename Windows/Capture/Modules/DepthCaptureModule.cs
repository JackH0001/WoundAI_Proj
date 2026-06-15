using Microsoft.Extensions.Logging;
using System.Drawing;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;
using Intel.RealSense;
using System.Runtime.InteropServices;

namespace WoundMeasurement.Capture.Modules
{
    /// <summary>
    /// 深度相機捕捉模組 (支援 Intel RealSense 和 Azure Kinect)
    /// </summary>
    public class DepthCaptureModule : ICaptureModule, IDisposable
    {
        private readonly ILogger<DepthCaptureModule> _logger;
        private Pipeline? _pipeline;
        private Config? _config;
        private bool _disposed = false;
        private bool _isRealSenseAvailable = false;
        private CaptureSettings? _settings;

        public DepthCaptureModule(ILogger<DepthCaptureModule> logger)
        {
            _logger = logger;
            CheckHardwareAvailability();
        }

        public string ModuleName => "Depth Camera Capture Module";

        public bool IsInitialized => _pipeline != null && _isRealSenseAvailable;

        public bool IsCapturing { get; private set; }

        public IEnumerable<Size> SupportedResolutions => new[]
        {
            new Size(640, 480),
            new Size(848, 480),
            new Size(1280, 720),
            new Size(1920, 1080)
        };

        public IEnumerable<int> SupportedFrameRates => new[]
        {
            15, 30, 60, 90
        };

        public bool SupportsDepthCapture => true;

        public event EventHandler<ImageData>? FrameCaptured;
        public event EventHandler<string>? ErrorOccurred;

        private void CheckHardwareAvailability()
        {
            try
            {
                // 檢查 Intel RealSense 設備
                var context = new Context();
                var devices = context.QueryDevices();
                
                if (devices.Count > 0)
                {
                    _isRealSenseAvailable = true;
                    _logger.LogInformation("找到 Intel RealSense 設備: {DeviceCount} 台", devices.Count);
                    
                    foreach (var device in devices)
                    {
                        _logger.LogInformation("設備: {Name}, 序號: {Serial}", 
                                             device.Info[CameraInfo.Name], 
                                             device.Info[CameraInfo.SerialNumber]);
                    }
                }
                else
                {
                    _logger.LogWarning("未找到 Intel RealSense 設備");
                }
                
                context.Dispose();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "檢查深度相機硬體時發生錯誤");
                _isRealSenseAvailable = false;
            }
        }

        public async Task<bool> InitializeAsync(CaptureSettings settings)
        {
            try
            {
                if (!_isRealSenseAvailable)
                {
                    _logger.LogError("深度相機硬體不可用");
                    return false;
                }

                _logger.LogInformation("初始化深度相機模組...");
                _settings = settings;

                _pipeline = new Pipeline();
                _config = new Config();

                // 設定彩色影像流
                _config.EnableStream(Stream.Color, 
                                   settings.Resolution.Width, 
                                   settings.Resolution.Height, 
                                   Format.Rgb8, 
                                   settings.FrameRate);

                // 設定深度影像流
                _config.EnableStream(Stream.Depth, 
                                   settings.Resolution.Width, 
                                   settings.Resolution.Height, 
                                   Format.Z16, 
                                   settings.FrameRate);

                // 啟動管道
                var profile = _pipeline.Start(_config);

                // 設定深度感測器參數
                ConfigureDepthSensor(profile);

                _logger.LogInformation("深度相機模組初始化成功");
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "深度相機模組初始化失敗");
                ErrorOccurred?.Invoke(this, ex.Message);
                return false;
            }
        }

        private void ConfigureDepthSensor(PipelineProfile profile)
        {
            try
            {
                var depthSensor = profile.Device.QuerySensors().FirstOrDefault(s => s.Is(Extension.DepthSensor));
                if (depthSensor != null)
                {
                    // 設定深度單位為毫米
                    if (depthSensor.Supports(Option.DepthUnits))
                    {
                        depthSensor.Options[Option.DepthUnits].Value = 0.001f; // 1mm
                    }

                    // 開啟高精度模式
                    if (depthSensor.Supports(Option.AccuracyMode))
                    {
                        depthSensor.Options[Option.AccuracyMode].Value = 3; // 高精度
                    }

                    // 設定深度範圍 (0.1m - 3m 適合傷口測量)
                    if (depthSensor.Supports(Option.MinDistance))
                    {
                        depthSensor.Options[Option.MinDistance].Value = 100; // 100mm
                    }

                    if (depthSensor.Supports(Option.MaxDistance))
                    {
                        depthSensor.Options[Option.MaxDistance].Value = 3000; // 3000mm
                    }

                    _logger.LogInformation("深度感測器參數設定完成");
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "設定深度感測器參數時發生警告");
            }
        }

        public async Task<bool> StartCaptureAsync()
        {
            if (!IsInitialized)
            {
                _logger.LogError("深度相機模組未初始化");
                return false;
            }

            try
            {
                IsCapturing = true;
                _logger.LogInformation("開始深度相機捕捉");

                // 在背景執行捕捉循環
                _ = Task.Run(DepthCaptureLoop);

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "開始深度捕捉失敗");
                IsCapturing = false;
                return false;
            }
        }

        public async Task<bool> StopCaptureAsync()
        {
            try
            {
                IsCapturing = false;
                _logger.LogInformation("停止深度相機捕捉");
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "停止深度捕捉失敗");
                return false;
            }
        }

        public async Task<ImageData?> CaptureSingleFrameAsync()
        {
            if (!IsInitialized)
            {
                _logger.LogError("深度相機模組未初始化");
                return null;
            }

            try
            {
                using var frames = _pipeline!.WaitForFrames();
                
                var colorFrame = frames.ColorFrame;
                var depthFrame = frames.DepthFrame;

                if (colorFrame == null || depthFrame == null)
                {
                    _logger.LogWarning("未能獲取完整的色彩和深度幀");
                    return null;
                }

                // 處理色彩影像
                var colorBitmap = ConvertColorFrameToBitmap(colorFrame);
                
                // 處理深度影像
                var depthData = ConvertDepthFrameToArray(depthFrame);
                var depthMetrics = AnalyzeDepthQuality(depthData, depthFrame.Width, depthFrame.Height);

                var imageData = new ImageData
                {
                    RgbImage = colorBitmap,
                    DepthData = depthData,
                    Width = colorFrame.Width,
                    Height = colorFrame.Height,
                    Timestamp = DateTime.Now,
                    QualityScore = CalculateDepthEnhancedQuality(colorBitmap, depthMetrics),
                    DepthMetrics = depthMetrics
                };

                _logger.LogDebug("深度影像捕捉成功: {Width}x{Height}, 深度品質: {DepthQuality:F2}", 
                               colorFrame.Width, colorFrame.Height, depthMetrics.OverallQuality);

                return imageData;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "深度影像捕捉失敗");
                ErrorOccurred?.Invoke(this, ex.Message);
                return null;
            }
        }

        private async Task DepthCaptureLoop()
        {
            while (IsCapturing && _pipeline != null)
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
                    _logger.LogError(ex, "深度捕捉循環中發生錯誤");
                    ErrorOccurred?.Invoke(this, ex.Message);
                    await Task.Delay(1000); // 錯誤時延遲 1 秒
                }
            }
        }

        private Bitmap ConvertColorFrameToBitmap(VideoFrame colorFrame)
        {
            var width = colorFrame.Width;
            var height = colorFrame.Height;
            var stride = colorFrame.Stride;

            var bitmap = new Bitmap(width, height, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
            var bitmapData = bitmap.LockBits(
                new Rectangle(0, 0, width, height),
                System.Drawing.Imaging.ImageLockMode.WriteOnly,
                System.Drawing.Imaging.PixelFormat.Format24bppRgb);

            var sourcePtr = colorFrame.Data;
            var destPtr = bitmapData.Scan0;

            // 複製像素數據
            unsafe
            {
                Buffer.MemoryCopy(sourcePtr.ToPointer(), destPtr.ToPointer(), 
                                bitmapData.Stride * height, stride * height);
            }

            bitmap.UnlockBits(bitmapData);
            return bitmap;
        }

        private float[] ConvertDepthFrameToArray(DepthFrame depthFrame)
        {
            var width = depthFrame.Width;
            var height = depthFrame.Height;
            var depthData = new float[width * height];

            var sourcePtr = depthFrame.Data;

            unsafe
            {
                var sourceShort = (ushort*)sourcePtr.ToPointer();
                for (int i = 0; i < depthData.Length; i++)
                {
                    // 轉換為公尺單位
                    depthData[i] = sourceShort[i] * 0.001f;
                }
            }

            return depthData;
        }

        private DepthQualityMetrics AnalyzeDepthQuality(float[] depthData, int width, int height)
        {
            var validPixels = depthData.Count(d => d > 0 && d < 3.0f); // 0-3公尺範圍內的有效像素
            var totalPixels = depthData.Length;
            var coverage = (double)validPixels / totalPixels;

            // 計算深度一致性 (相鄰像素深度差異)
            var consistency = CalculateDepthConsistency(depthData, width, height);

            // 計算深度準確度 (基於深度梯度)
            var accuracy = CalculateDepthAccuracy(depthData, width, height);

            // 計算深度雜訊水平
            var noiseLevel = CalculateDepthNoise(depthData, width, height);

            var overallQuality = (coverage * 0.3 + consistency * 0.3 + accuracy * 0.2 + (1.0 - noiseLevel) * 0.2) * 100;

            return new DepthQualityMetrics
            {
                Coverage = coverage,
                Consistency = consistency,
                Accuracy = accuracy,
                NoiseLevel = noiseLevel,
                OverallQuality = overallQuality,
                ValidPixelCount = validPixels,
                TotalPixelCount = totalPixels
            };
        }

        private double CalculateDepthConsistency(float[] depthData, int width, int height)
        {
            double totalDifference = 0;
            int comparisonCount = 0;

            for (int y = 0; y < height - 1; y++)
            {
                for (int x = 0; x < width - 1; x++)
                {
                    var currentIdx = y * width + x;
                    var rightIdx = y * width + x + 1;
                    var downIdx = (y + 1) * width + x;

                    var current = depthData[currentIdx];
                    var right = depthData[rightIdx];
                    var down = depthData[downIdx];

                    if (current > 0 && right > 0)
                    {
                        totalDifference += Math.Abs(current - right);
                        comparisonCount++;
                    }

                    if (current > 0 && down > 0)
                    {
                        totalDifference += Math.Abs(current - down);
                        comparisonCount++;
                    }
                }
            }

            var avgDifference = comparisonCount > 0 ? totalDifference / comparisonCount : 1.0;
            return Math.Max(0, 1.0 - avgDifference * 10); // 差異越小一致性越高
        }

        private double CalculateDepthAccuracy(float[] depthData, int width, int height)
        {
            // 基於深度梯度的準確度評估
            double gradientSum = 0;
            int gradientCount = 0;

            for (int y = 1; y < height - 1; y++)
            {
                for (int x = 1; x < width - 1; x++)
                {
                    var centerIdx = y * width + x;
                    if (depthData[centerIdx] <= 0) continue;

                    var gradX = depthData[centerIdx + 1] - depthData[centerIdx - 1];
                    var gradY = depthData[(y + 1) * width + x] - depthData[(y - 1) * width + x];
                    var gradient = Math.Sqrt(gradX * gradX + gradY * gradY);

                    gradientSum += gradient;
                    gradientCount++;
                }
            }

            var avgGradient = gradientCount > 0 ? gradientSum / gradientCount : 0;
            return Math.Min(1.0, avgGradient * 5); // 適度的梯度表示良好的深度準確度
        }

        private double CalculateDepthNoise(float[] depthData, int width, int height)
        {
            // 使用局部標準差估計雜訊
            double noiseSum = 0;
            int noiseCount = 0;

            for (int y = 1; y < height - 1; y++)
            {
                for (int x = 1; x < width - 1; x++)
                {
                    var centerIdx = y * width + x;
                    if (depthData[centerIdx] <= 0) continue;

                    // 計算 3x3 鄰域的標準差
                    var neighbors = new List<float>();
                    for (int dy = -1; dy <= 1; dy++)
                    {
                        for (int dx = -1; dx <= 1; dx++)
                        {
                            var neighborIdx = (y + dy) * width + (x + dx);
                            if (depthData[neighborIdx] > 0)
                            {
                                neighbors.Add(depthData[neighborIdx]);
                            }
                        }
                    }

                    if (neighbors.Count >= 5)
                    {
                        var mean = neighbors.Average();
                        var variance = neighbors.Sum(n => Math.Pow(n - mean, 2)) / neighbors.Count;
                        var stdDev = Math.Sqrt(variance);

                        noiseSum += stdDev;
                        noiseCount++;
                    }
                }
            }

            return noiseCount > 0 ? Math.Min(1.0, noiseSum / noiseCount * 50) : 0.5;
        }

        private double CalculateDepthEnhancedQuality(Bitmap colorBitmap, DepthQualityMetrics depthMetrics)
        {
            // 結合色彩影像品質和深度品質的綜合評分
            var colorQuality = CalculateColorImageQuality(colorBitmap);
            var depthWeight = 0.4;
            var colorWeight = 0.6;

            return colorQuality * colorWeight + depthMetrics.OverallQuality * depthWeight;
        }

        private double CalculateColorImageQuality(Bitmap bitmap)
        {
            // 簡化的色彩影像品質評估
            // 在實際實作中可以整合 OpenCV 品質評估算法
            return 85.0; // 暫時返回固定值
        }

        public void Dispose()
        {
            if (!_disposed)
            {
                IsCapturing = false;
                _pipeline?.Stop();
                _pipeline?.Dispose();
                _config?.Dispose();
                _disposed = true;
            }
        }
    }

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
}