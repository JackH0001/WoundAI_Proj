using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Services
{
    /// <summary>
    /// 即時分析服務 - 移植自iOS RealTimeAnalysisModule
    /// </summary>
    public class RealTimeAnalysisService : IDisposable
    {
        private readonly ILogger<RealTimeAnalysisService> _logger;
        private readonly SegmentationEngine _segmentationEngine;
        private readonly MeasurementEngine _measurementEngine;
        
        // 分析設定
        private const int ANALYSIS_INTERVAL_MS = 1000; // 1秒
        private const int MAX_CACHE_SIZE = 10;
        private const int MAX_HISTORY_SIZE = 50;
        
        // 狀態管理
        private bool _isAnalyzing = false;
        private RealTimeAnalysisResult? _currentAnalysis;
        private readonly List<RealTimeAnalysisResult> _analysisHistory = new();
        private readonly ConcurrentDictionary<string, RealTimeAnalysisResult> _cachedResults = new();
        
        // 任務管理
        private CancellationTokenSource? _analysisCancellationTokenSource;
        private long _lastAnalysisTime = 0;
        
        // 事件
        public event EventHandler<RealTimeAnalysisResult>? AnalysisCompleted;
        public event EventHandler<bool>? AnalysisStatusChanged;

        public RealTimeAnalysisService(ILogger<RealTimeAnalysisService> logger)
        {
            _logger = logger;
            _segmentationEngine = new SegmentationEngine();
            _measurementEngine = new MeasurementEngine();
        }

        /// <summary>
        /// 開始即時分析
        /// </summary>
        public async Task StartRealTimeAnalysisAsync(Func<Bitmap?> imageStream)
        {
            await StopRealTimeAnalysisAsync();
            
            _analysisCancellationTokenSource = new CancellationTokenSource();
            var cancellationToken = _analysisCancellationTokenSource.Token;
            
            _logger.LogInformation("開始即時分析");
            
            _ = Task.Run(async () =>
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    try
                    {
                        var image = imageStream();
                        if (image != null)
                        {
                            var currentTime = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                            
                            // 控制分析頻率
                            if (currentTime - _lastAnalysisTime >= ANALYSIS_INTERVAL_MS)
                            {
                                await PerformQuickAnalysisAsync(image);
                                _lastAnalysisTime = currentTime;
                            }
                        }
                        
                        await Task.Delay(100, cancellationToken); // 100ms檢查間隔
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError(ex, "即時分析過程中發生錯誤");
                    }
                }
            }, cancellationToken);
        }

        /// <summary>
        /// 停止即時分析
        /// </summary>
        public async Task StopRealTimeAnalysisAsync()
        {
            _analysisCancellationTokenSource?.Cancel();
            _analysisCancellationTokenSource?.Dispose();
            _analysisCancellationTokenSource = null;
            
            _isAnalyzing = false;
            OnAnalysisStatusChanged(false);
            
            _logger.LogInformation("即時分析已停止");
            await Task.CompletedTask;
        }

        /// <summary>
        /// 執行快速分析
        /// </summary>
        private async Task PerformQuickAnalysisAsync(Bitmap image)
        {
            var startTime = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            
            try
            {
                _isAnalyzing = true;
                OnAnalysisStatusChanged(true);
                
                // 1. 快速品質評估
                var quality = await AssessImageQualityAsync(image);
                
                // 2. 快速傷口偵測
                var woundDetection = await DetectWoundPresenceAsync(image);
                
                // 3. 如果有傷口，進行快速測量
                double? estimatedArea = null;
                double? estimatedVolume = null;
                string? woundType = null;
                
                if (woundDetection.HasWound && woundDetection.Confidence > 0.6)
                {
                    var quickMeasurement = await PerformQuickMeasurementAsync(image);
                    estimatedArea = quickMeasurement.Area;
                    estimatedVolume = quickMeasurement.Volume;
                    woundType = quickMeasurement.Type;
                }
                
                var processingTime = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - startTime;
                
                var result = new RealTimeAnalysisResult
                {
                    Timestamp = DateTime.Now,
                    HasWound = woundDetection.HasWound,
                    Confidence = woundDetection.Confidence,
                    EstimatedArea = estimatedArea,
                    EstimatedVolume = estimatedVolume,
                    WoundType = woundType,
                    Quality = quality,
                    ProcessingTime = processingTime
                };
                
                // 更新當前分析結果
                _currentAnalysis = result;
                
                // 添加到歷史記錄
                lock (_analysisHistory)
                {
                    _analysisHistory.Insert(0, result);
                    if (_analysisHistory.Count > MAX_HISTORY_SIZE)
                    {
                        _analysisHistory.RemoveAt(_analysisHistory.Count - 1);
                    }
                }
                
                // 緩存結果
                CacheResult(result);
                
                // 觸發事件
                OnAnalysisCompleted(result);
                
                _logger.LogDebug("快速分析完成: 傷口={HasWound}, 置信度={Confidence}", 
                    woundDetection.HasWound, woundDetection.Confidence);
                
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "快速分析失敗");
            }
            finally
            {
                _isAnalyzing = false;
                OnAnalysisStatusChanged(false);
            }
        }

        /// <summary>
        /// 圖像品質評估
        /// </summary>
        private async Task<string> AssessImageQualityAsync(Bitmap image)
        {
            return await Task.Run(() =>
            {
                try
                {
                    // 基礎品質檢查
                    var resolution = image.Width * image.Height;
                    
                    // 解析度檢查
                    if (resolution < 640 * 480)
                    {
                        return "低";
                    }
                    else if (resolution < 1920 * 1080)
                    {
                        return "中";
                    }
                    else
                    {
                        return "高";
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "品質評估失敗");
                    return "未知";
                }
            });
        }

        /// <summary>
        /// 傷口存在偵測
        /// </summary>
        private async Task<WoundDetectionResult> DetectWoundPresenceAsync(Bitmap image)
        {
            return await Task.Run(() =>
            {
                try
                {
                    // 使用OpenCV進行基礎傷口偵測
                    var hasWound = _segmentationEngine.DetectWound(image);
                    var confidence = hasWound ? 0.8 : 0.2;
                    
                    return new WoundDetectionResult(hasWound, confidence);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "傷口偵測失敗");
                    return new WoundDetectionResult(false, 0.0);
                }
            });
        }

        /// <summary>
        /// 快速測量
        /// </summary>
        private async Task<QuickMeasurementResult> PerformQuickMeasurementAsync(Bitmap image)
        {
            return await Task.Run(() =>
            {
                try
                {
                    var area = _measurementEngine.EstimateArea(image);
                    var volume = _measurementEngine.EstimateVolume(image);
                    var type = _measurementEngine.ClassifyWoundType(image);
                    
                    return new QuickMeasurementResult(area, volume, type);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "快速測量失敗");
                    return new QuickMeasurementResult(null, null, null);
                }
            });
        }

        /// <summary>
        /// 緩存結果
        /// </summary>
        private void CacheResult(RealTimeAnalysisResult result)
        {
            var key = result.Timestamp.Ticks.ToString();
            _cachedResults[key] = result;
            
            // 清理過期緩存
            if (_cachedResults.Count > MAX_CACHE_SIZE)
            {
                var oldestKey = _cachedResults.Keys.First();
                _cachedResults.TryRemove(oldestKey, out _);
            }
        }

        /// <summary>
        /// 獲取當前分析結果
        /// </summary>
        public RealTimeAnalysisResult? GetCurrentAnalysis()
        {
            return _currentAnalysis;
        }

        /// <summary>
        /// 獲取分析歷史
        /// </summary>
        public IReadOnlyList<RealTimeAnalysisResult> GetAnalysisHistory()
        {
            lock (_analysisHistory)
            {
                return _analysisHistory.ToList().AsReadOnly();
            }
        }

        /// <summary>
        /// 獲取分析狀態
        /// </summary>
        public bool IsAnalyzing => _isAnalyzing;

        // 事件觸發方法
        protected virtual void OnAnalysisCompleted(RealTimeAnalysisResult result)
        {
            AnalysisCompleted?.Invoke(this, result);
        }

        protected virtual void OnAnalysisStatusChanged(bool isAnalyzing)
        {
            AnalysisStatusChanged?.Invoke(this, isAnalyzing);
        }

        public void Dispose()
        {
            _analysisCancellationTokenSource?.Cancel();
            _analysisCancellationTokenSource?.Dispose();
            _cachedResults.Clear();
            _analysisHistory.Clear();
        }
    }

    /// <summary>
    /// 即時分析結果
    /// </summary>
    public class RealTimeAnalysisResult
    {
        public DateTime Timestamp { get; set; }
        public bool HasWound { get; set; }
        public double Confidence { get; set; }
        public double? EstimatedArea { get; set; } // cm²
        public double? EstimatedVolume { get; set; } // cm³
        public string? WoundType { get; set; }
        public string Quality { get; set; } = string.Empty;
        public long ProcessingTime { get; set; }
    }

    /// <summary>
    /// 傷口偵測結果
    /// </summary>
    public class WoundDetectionResult
    {
        public bool HasWound { get; }
        public double Confidence { get; }

        public WoundDetectionResult(bool hasWound, double confidence)
        {
            HasWound = hasWound;
            Confidence = confidence;
        }
    }

    /// <summary>
    /// 快速測量結果
    /// </summary>
    public class QuickMeasurementResult
    {
        public double? Area { get; }
        public double? Volume { get; }
        public string? Type { get; }

        public QuickMeasurementResult(double? area, double? volume, string? type)
        {
            Area = area;
            Volume = volume;
            Type = type;
        }
    }

    /// <summary>
    /// 分割引擎（簡化版本）
    /// </summary>
    internal class SegmentationEngine
    {
        public bool DetectWound(Bitmap image)
        {
            // 簡化的傷口偵測邏輯
            // 實際實作中應使用ML模型
            return true; // 暫時返回true
        }
    }

    /// <summary>
    /// 測量引擎（簡化版本）
    /// </summary>
    internal class MeasurementEngine
    {
        public double? EstimateArea(Bitmap image)
        {
            // 簡化的面積估算
            return 10.0; // 暫時返回固定值
        }

        public double? EstimateVolume(Bitmap image)
        {
            // 簡化的體積估算
            return 1.0; // 暫時返回固定值
        }

        public string? ClassifyWoundType(Bitmap image)
        {
            // 簡化的傷口分類
            return "慢性傷口"; // 暫時返回固定值
        }
    }
} 