using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Services
{
    /// <summary>
    /// 傷口量測系統主管理器
    /// </summary>
    public class WoundMeasurementSystem : IDisposable
    {
        private readonly IServiceProvider _serviceProvider;
        private readonly ILogger<WoundMeasurementSystem> _logger;
        private readonly Dictionary<string, object> _modules = new();

        public WoundMeasurementSystem(IServiceProvider serviceProvider, ILogger<WoundMeasurementSystem> logger)
        {
            _serviceProvider = serviceProvider;
            _logger = logger;
        }

        /// <summary>
        /// 系統是否已初始化
        /// </summary>
        public bool IsInitialized { get; private set; }

        /// <summary>
        /// 捕捉模組
        /// </summary>
        public ICaptureModule? CaptureModule { get; private set; }

        /// <summary>
        /// 處理模組
        /// </summary>
        public IProcessingModule? ProcessingModule { get; private set; }

        /// <summary>
        /// AI 模組
        /// </summary>
        public IAIModule? AIModule { get; private set; }

        /// <summary>
        /// 量測模組
        /// </summary>
        public IMeasurementModule? MeasurementModule { get; private set; }

        /// <summary>
        /// 初始化系統
        /// </summary>
        /// <param name="settings">系統設定</param>
        /// <returns>初始化是否成功</returns>
        public async Task<bool> InitializeAsync(SystemSettings settings)
        {
            try
            {
                _logger.LogInformation("開始初始化傷口量測系統...");

                // 初始化捕捉模組
                if (settings.EnableCaptureModule)
                {
                    CaptureModule = _serviceProvider.GetService<ICaptureModule>();
                    if (CaptureModule != null)
                    {
                        var captureInitialized = await CaptureModule.InitializeAsync(settings.CaptureSettings);
                        if (!captureInitialized)
                        {
                            _logger.LogError("捕捉模組初始化失敗");
                            return false;
                        }
                        _logger.LogInformation("捕捉模組初始化成功");
                    }
                }

                // 初始化處理模組
                if (settings.EnableProcessingModule)
                {
                    ProcessingModule = _serviceProvider.GetService<IProcessingModule>();
                    if (ProcessingModule != null)
                    {
                        var processingInitialized = await ProcessingModule.InitializeAsync(settings.ProcessingSettings);
                        if (!processingInitialized)
                        {
                            _logger.LogError("處理模組初始化失敗");
                            return false;
                        }
                        _logger.LogInformation("處理模組初始化成功");
                    }
                }

                // 初始化 AI 模組
                if (settings.EnableAIModule)
                {
                    AIModule = _serviceProvider.GetService<IAIModule>();
                    if (AIModule != null)
                    {
                        var aiInitialized = await AIModule.InitializeAsync(settings.AISettings);
                        if (!aiInitialized)
                        {
                            _logger.LogError("AI 模組初始化失敗");
                            return false;
                        }
                        _logger.LogInformation("AI 模組初始化成功");
                    }
                }

                // 初始化量測模組
                if (settings.EnableMeasurementModule)
                {
                    MeasurementModule = _serviceProvider.GetService<IMeasurementModule>();
                    if (MeasurementModule != null)
                    {
                        var measurementInitialized = await MeasurementModule.InitializeAsync(settings.MeasurementSettings);
                        if (!measurementInitialized)
                        {
                            _logger.LogError("量測模組初始化失敗");
                            return false;
                        }
                        _logger.LogInformation("量測模組初始化成功");
                    }
                }

                IsInitialized = true;
                _logger.LogInformation("傷口量測系統初始化完成");
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "系統初始化過程中發生錯誤");
                return false;
            }
        }

        /// <summary>
        /// 執行完整的傷口量測流程
        /// </summary>
        /// <returns>處理結果</returns>
        public async Task<ProcessingResult> ProcessWoundMeasurementAsync()
        {
            var result = new ProcessingResult();
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            try
            {
                if (!IsInitialized)
                {
                    result.IsSuccess = false;
                    result.ErrorMessage = "系統尚未初始化";
                    return result;
                }

                _logger.LogInformation("開始執行傷口量測流程...");

                // 步驟 1: 捕捉影像
                if (CaptureModule == null)
                {
                    result.IsSuccess = false;
                    result.ErrorMessage = "捕捉模組未初始化";
                    return result;
                }

                var capturedImage = await CaptureModule.CaptureSingleFrameAsync();
                if (capturedImage == null)
                {
                    result.IsSuccess = false;
                    result.ErrorMessage = "影像捕捉失敗";
                    return result;
                }

                result.OriginalImage = capturedImage;
                _logger.LogInformation("影像捕捉成功");

                // 步驟 2: 影像處理
                if (ProcessingModule != null)
                {
                    var processedResult = await ProcessingModule.ProcessImageAsync(capturedImage);
                    if (processedResult.IsSuccess)
                    {
                        result.ProcessedImage = processedResult.ProcessedImage;
                        _logger.LogInformation("影像處理完成");
                    }
                    else
                    {
                        _logger.LogWarning("影像處理失敗: {Error}", processedResult.ErrorMessage);
                    }
                }

                // 步驟 3: AI 分類和分割
                if (AIModule != null)
                {
                    // 分類
                    var classification = await AIModule.ClassifyWoundAsync(capturedImage);
                    result.Classification = classification;
                    _logger.LogInformation("傷口分類完成: {Type}", classification.PredictedType);

                    // 分割
                    var segmentationMask = await AIModule.SegmentWoundAsync(capturedImage);
                    if (segmentationMask != null)
                    {
                        _logger.LogInformation("傷口分割完成");
                    }
                }

                // 步驟 4: 量測
                if (MeasurementModule != null && result.Classification != null)
                {
                    var segmentationMask = await AIModule?.SegmentWoundAsync(capturedImage);
                    if (segmentationMask != null)
                    {
                        var measurement = await MeasurementModule.MeasureWoundAsync(capturedImage, segmentationMask);
                        result.Measurement = measurement;
                        _logger.LogInformation("傷口量測完成: 面積={Area:F2}mm², 周長={Perimeter:F2}mm", 
                            measurement.Area, measurement.Perimeter);
                    }
                }

                result.IsSuccess = true;
                _logger.LogInformation("傷口量測流程執行完成");
            }
            catch (Exception ex)
            {
                result.IsSuccess = false;
                result.ErrorMessage = ex.Message;
                _logger.LogError(ex, "傷口量測流程執行失敗");
            }
            finally
            {
                stopwatch.Stop();
                result.ProcessingTimeMs = stopwatch.ElapsedMilliseconds;
            }

            return result;
        }

        /// <summary>
        /// 開始即時捕捉
        /// </summary>
        /// <returns>開始捕捉是否成功</returns>
        public async Task<bool> StartRealTimeCaptureAsync()
        {
            if (CaptureModule == null)
            {
                _logger.LogError("捕捉模組未初始化");
                return false;
            }

            try
            {
                var success = await CaptureModule.StartCaptureAsync();
                if (success)
                {
                    _logger.LogInformation("即時捕捉已開始");
                }
                return success;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "開始即時捕捉失敗");
                return false;
            }
        }

        /// <summary>
        /// 停止即時捕捉
        /// </summary>
        /// <returns>停止捕捉是否成功</returns>
        public async Task<bool> StopRealTimeCaptureAsync()
        {
            if (CaptureModule == null)
            {
                return false;
            }

            try
            {
                var success = await CaptureModule.StopCaptureAsync();
                if (success)
                {
                    _logger.LogInformation("即時捕捉已停止");
                }
                return success;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "停止即時捕捉失敗");
                return false;
            }
        }

        /// <summary>
        /// 獲取系統狀態
        /// </summary>
        /// <returns>系統狀態</returns>
        public SystemStatus GetSystemStatus()
        {
            return new SystemStatus
            {
                IsInitialized = IsInitialized,
                CaptureModuleStatus = CaptureModule?.IsInitialized ?? false,
                ProcessingModuleStatus = ProcessingModule?.IsInitialized ?? false,
                AIModuleStatus = AIModule?.IsInitialized ?? false,
                MeasurementModuleStatus = MeasurementModule?.IsInitialized ?? false,
                IsCapturing = CaptureModule?.IsCapturing ?? false,
                Timestamp = DateTime.Now
            };
        }

        public void Dispose()
        {
            CaptureModule?.Dispose();
            ProcessingModule?.Dispose();
            AIModule?.Dispose();
            MeasurementModule?.Dispose();
            GC.SuppressFinalize(this);
        }
    }

    /// <summary>
    /// 系統設定
    /// </summary>
    public class SystemSettings
    {
        /// <summary>
        /// 是否啟用捕捉模組
        /// </summary>
        public bool EnableCaptureModule { get; set; } = true;

        /// <summary>
        /// 是否啟用處理模組
        /// </summary>
        public bool EnableProcessingModule { get; set; } = true;

        /// <summary>
        /// 是否啟用 AI 模組
        /// </summary>
        public bool EnableAIModule { get; set; } = true;

        /// <summary>
        /// 是否啟用量測模組
        /// </summary>
        public bool EnableMeasurementModule { get; set; } = true;

        /// <summary>
        /// 捕捉設定
        /// </summary>
        public CaptureSettings CaptureSettings { get; set; } = new();

        /// <summary>
        /// 處理設定
        /// </summary>
        public ProcessingSettings ProcessingSettings { get; set; } = new();

        /// <summary>
        /// AI 設定
        /// </summary>
        public AISettings AISettings { get; set; } = new();

        /// <summary>
        /// 量測設定
        /// </summary>
        public MeasurementSettings MeasurementSettings { get; set; } = new();
    }

    /// <summary>
    /// 系統狀態
    /// </summary>
    public class SystemStatus
    {
        /// <summary>
        /// 系統是否已初始化
        /// </summary>
        public bool IsInitialized { get; set; }

        /// <summary>
        /// 捕捉模組狀態
        /// </summary>
        public bool CaptureModuleStatus { get; set; }

        /// <summary>
        /// 處理模組狀態
        /// </summary>
        public bool ProcessingModuleStatus { get; set; }

        /// <summary>
        /// AI 模組狀態
        /// </summary>
        public bool AIModuleStatus { get; set; }

        /// <summary>
        /// 量測模組狀態
        /// </summary>
        public bool MeasurementModuleStatus { get; set; }

        /// <summary>
        /// 是否正在捕捉
        /// </summary>
        public bool IsCapturing { get; set; }

        /// <summary>
        /// 時間戳記
        /// </summary>
        public DateTime Timestamp { get; set; }
    }
} 