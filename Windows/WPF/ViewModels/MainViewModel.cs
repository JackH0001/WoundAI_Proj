using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Media.Imaging;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;
using WoundMeasurement.Core.Services;
// alias 避免「namespace WoundMeasurement」與「class WoundMeasurement」名稱衝突（同 IMeasurementModule.cs 慣例）
using WoundMeasurementModel = WoundMeasurement.Core.Models.WoundMeasurement;

namespace WoundMeasurement.WPF.ViewModels
{
    /// <summary>
    /// 主視窗的 ViewModel
    /// </summary>
    public partial class MainViewModel : ObservableObject
    {
        private readonly WoundMeasurementSystem _system;
        private readonly ILogger<MainViewModel> _logger;

        public MainViewModel(WoundMeasurementSystem system, ILogger<MainViewModel> logger)
        {
            _system = system;
            _logger = logger;
            RecentMeasurements = new ObservableCollection<WoundMeasurementModel>();

            // 初始化系統
            _ = InitializeSystemAsync();
        }

        #region 屬性

        [ObservableProperty]
        private SystemStatus _systemStatus = new();

        [ObservableProperty]
        private BitmapSource? _originalImage;

        [ObservableProperty]
        private BitmapSource? _processedImage;

        [ObservableProperty]
        private string _originalImageInfo = "未捕捉影像";

        [ObservableProperty]
        private string _processedImageInfo = "未處理影像";

        [ObservableProperty]
        private WoundMeasurementModel _measurementResult = new();

        [ObservableProperty]
        private string _statusMessage = "系統就緒";

        [ObservableProperty]
        private long _processingTime;

        [ObservableProperty]
        private double _progressValue;

        [ObservableProperty]
        private bool _canStartCapture = true;

        [ObservableProperty]
        private bool _canStopCapture = false;

        [ObservableProperty]
        private bool _canPerformMeasurement = false;

        public ObservableCollection<WoundMeasurementModel> RecentMeasurements { get; }

        #endregion

        #region 命令

        [RelayCommand]
        private async Task StartCapture()
        {
            try
            {
                StatusMessage = "正在啟動捕捉...";
                ProgressValue = 10;

                var success = await _system.StartRealTimeCaptureAsync();
                if (success)
                {
                    CanStartCapture = false;
                    CanStopCapture = true;
                    CanPerformMeasurement = true;
                    StatusMessage = "捕捉已開始";
                    ProgressValue = 100;

                    _logger.LogInformation("捕捉已開始");
                }
                else
                {
                    StatusMessage = "啟動捕捉失敗";
                    ProgressValue = 0;
                    MessageBox.Show("啟動捕捉失敗", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "啟動捕捉時發生錯誤");
                StatusMessage = "啟動捕捉時發生錯誤";
                ProgressValue = 0;
                MessageBox.Show($"啟動捕捉時發生錯誤: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        [RelayCommand]
        private async Task StopCapture()
        {
            try
            {
                StatusMessage = "正在停止捕捉...";
                ProgressValue = 10;

                var success = await _system.StopRealTimeCaptureAsync();
                if (success)
                {
                    CanStartCapture = true;
                    CanStopCapture = false;
                    StatusMessage = "捕捉已停止";
                    ProgressValue = 100;

                    _logger.LogInformation("捕捉已停止");
                }
                else
                {
                    StatusMessage = "停止捕捉失敗";
                    ProgressValue = 0;
                    MessageBox.Show("停止捕捉失敗", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "停止捕捉時發生錯誤");
                StatusMessage = "停止捕捉時發生錯誤";
                ProgressValue = 0;
                MessageBox.Show($"停止捕捉時發生錯誤: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        [RelayCommand]
        private async Task SingleMeasurement()
        {
            try
            {
                StatusMessage = "正在執行單次量測...";
                ProgressValue = 0;

                var result = await _system.ProcessWoundMeasurementAsync();
                if (result.IsSuccess)
                {
                    // 更新影像顯示
                    if (result.OriginalImage?.RgbImage != null)
                    {
                        OriginalImage = ConvertBitmapToBitmapSource(result.OriginalImage.RgbImage);
                        OriginalImageInfo = $"{result.OriginalImage.Width} x {result.OriginalImage.Height} | 品質: {result.OriginalImage.QualityScore:F1}";
                    }

                    if (result.ProcessedImage?.RgbImage != null)
                    {
                        ProcessedImage = ConvertBitmapToBitmapSource(result.ProcessedImage.RgbImage);
                        ProcessedImageInfo = $"{result.ProcessedImage.Width} x {result.ProcessedImage.Height} | 品質: {result.ProcessedImage.QualityScore:F1}";
                    }

                    // 更新量測結果
                    if (result.Measurement != null)
                    {
                        MeasurementResult = result.Measurement;
                        
                        // 添加到最近量測列表
                        RecentMeasurements.Insert(0, result.Measurement);
                        if (RecentMeasurements.Count > 10)
                        {
                            RecentMeasurements.RemoveAt(RecentMeasurements.Count - 1);
                        }
                    }

                    ProcessingTime = result.ProcessingTimeMs;
                    StatusMessage = $"量測完成 - 面積: {result.Measurement?.Area:F2} mm²";
                    ProgressValue = 100;

                    _logger.LogInformation("單次量測完成");
                }
                else
                {
                    StatusMessage = $"量測失敗: {result.ErrorMessage}";
                    ProgressValue = 0;
                    MessageBox.Show($"量測失敗: {result.ErrorMessage}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "執行單次量測時發生錯誤");
                StatusMessage = "執行量測時發生錯誤";
                ProgressValue = 0;
                MessageBox.Show($"執行量測時發生錯誤: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        [RelayCommand]
        private async Task ContinuousMeasurement()
        {
            try
            {
                StatusMessage = "正在啟動連續量測...";
                ProgressValue = 10;

                // 這裡可以實作連續量測邏輯
                // 例如：每 5 秒自動執行一次量測
                MessageBox.Show("連續量測功能開發中...", "提示", MessageBoxButton.OK, MessageBoxImage.Information);

                StatusMessage = "連續量測已啟動";
                ProgressValue = 100;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "啟動連續量測時發生錯誤");
                StatusMessage = "啟動連續量測時發生錯誤";
                ProgressValue = 0;
                MessageBox.Show($"啟動連續量測時發生錯誤: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        [RelayCommand]
        private void OpenSettings()
        {
            MessageBox.Show("設定功能開發中...", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
        }

        [RelayCommand]
        private void OpenHelp()
        {
            MessageBox.Show("說明功能開發中...", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
        }

        #endregion

        #region 私有方法

        private async Task InitializeSystemAsync()
        {
            try
            {
                StatusMessage = "正在初始化系統...";
                ProgressValue = 10;

                var settings = new SystemSettings
                {
                    EnableCaptureModule = true,
                    EnableProcessingModule = true,
                    EnableAIModule = true,
                    EnableMeasurementModule = true,
                    CaptureSettings = new CaptureSettings
                    {
                        Resolution = new System.Drawing.Size(640, 480),
                        FrameRate = 30,
                        EnableDepthCapture = false,
                        EnableConfidenceCapture = false,
                        ImageFormat = ImageFormat.RGB,
                        AutoExposure = true,
                        AutoWhiteBalance = true
                    },
                    ProcessingSettings = new ProcessingSettings
                    {
                        EnableWhiteBalance = true,
                        EnableGammaCorrection = true,
                        EnableNoiseReduction = true,
                        MinQualityScore = 20.0,
                        MinDepthCoverage = 0.8,
                        MinConfidence = 0.7
                    },
                    AISettings = new AISettings
                    {
                        ModelPath = "Models/wound_classification.onnx",
                        Engine = InferenceEngine.ONNX,
                        UseGPU = false,
                        MinConfidenceThreshold = 0.5
                    },
                    MeasurementSettings = new MeasurementSettings
                    {
                        PixelSizeMm = 0.1,
                        EnableDepthMeasurement = true,
                        EnableVolumeMeasurement = true,
                        MinAreaThreshold = 1.0,
                        MaxAreaThreshold = 10000.0
                    }
                };

                var success = await _system.InitializeAsync(settings);
                if (success)
                {
                    SystemStatus = _system.GetSystemStatus();
                    StatusMessage = "系統初始化完成";
                    ProgressValue = 100;
                    CanPerformMeasurement = true;

                    _logger.LogInformation("系統初始化完成");
                }
                else
                {
                    StatusMessage = "系統初始化失敗";
                    ProgressValue = 0;
                    MessageBox.Show("系統初始化失敗", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "系統初始化時發生錯誤");
                StatusMessage = "系統初始化時發生錯誤";
                ProgressValue = 0;
                MessageBox.Show($"系統初始化時發生錯誤: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        private BitmapSource ConvertBitmapToBitmapSource(System.Drawing.Bitmap bitmap)
        {
            try
            {
                using var memory = new System.IO.MemoryStream();
                bitmap.Save(memory, System.Drawing.Imaging.ImageFormat.Png);
                memory.Position = 0;

                var bitmapImage = new BitmapImage();
                bitmapImage.BeginInit();
                bitmapImage.CacheOption = BitmapCacheOption.OnLoad;
                bitmapImage.StreamSource = memory;
                bitmapImage.EndInit();
                bitmapImage.Freeze();

                return bitmapImage;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "轉換 Bitmap 時發生錯誤");
                return null!;
            }
        }

        #endregion
    }
} 