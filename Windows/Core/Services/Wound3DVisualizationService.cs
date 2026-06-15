using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using System.Numerics;
using System.IO;

namespace WoundMeasurement.Core.Services
{
    /// <summary>
    /// 傷口3D視覺化服務 - 移植自iOS Depth3DVisualizationView
    /// </summary>
    public class Wound3DVisualizationService : IDisposable
    {
        private readonly ILogger<Wound3DVisualizationService> _logger;
        
        // 狀態管理
        private bool _isGenerating3D = false;
        private float _currentRotationX = 0f;
        private float _currentRotationY = 0f;
        private float _zoomScale = 1.0f;
        
        // 事件
        public event EventHandler<Wound3DVisualizationResult>? VisualizationCompleted;
        public event EventHandler<bool>? GenerationStatusChanged;

        public Wound3DVisualizationService(ILogger<Wound3DVisualizationService> logger)
        {
            _logger = logger;
        }

        /// <summary>
        /// 生成3D視覺化
        /// </summary>
        public async Task<Wound3DVisualizationResult> Generate3DVisualizationAsync(
            byte[] depthData, 
            double woundArea)
        {
            return await Task.Run(async () =>
            {
                try
                {
                    _isGenerating3D = true;
                    OnGenerationStatusChanged(true);
                    
                    _logger.LogInformation("開始生成3D視覺化，傷口面積: {WoundArea} cm²", woundArea);

                    // 驗證深度數據
                    if (!ValidateDepthData(depthData))
                    {
                        throw new InvalidOperationException("無效的深度數據");
                    }

                    // 處理深度數據
                    var processedDepthData = ProcessDepthData(depthData);
                    
                    // 生成3D模型
                    var modelData = Generate3DModel(processedDepthData, woundArea);
                    
                    // 計算統計信息
                    var statistics = CalculateDepthStatistics(processedDepthData, woundArea);

                    var result = new Wound3DVisualizationResult
                    {
                        ModelData = modelData,
                        Statistics = statistics,
                        GenerationTime = DateTime.Now,
                        Success = true
                    };

                    _logger.LogInformation("3D視覺化生成成功");
                    OnVisualizationCompleted(result);
                    
                    return result;

                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "3D視覺化生成失敗");
                    
                    var errorResult = new Wound3DVisualizationResult
                    {
                        ModelData = null,
                        Statistics = null,
                        GenerationTime = DateTime.Now,
                        Success = false,
                        ErrorMessage = ex.Message
                    };
                    
                    OnVisualizationCompleted(errorResult);
                    return errorResult;
                }
                finally
                {
                    _isGenerating3D = false;
                    OnGenerationStatusChanged(false);
                }
            });
        }

        /// <summary>
        /// 重置視圖
        /// </summary>
        public void ResetView()
        {
            _currentRotationX = 0f;
            _currentRotationY = 0f;
            _zoomScale = 1.0f;
            
            _logger.LogDebug("3D視圖已重置");
        }

        /// <summary>
        /// 更新旋轉
        /// </summary>
        public void UpdateRotation(float deltaX, float deltaY)
        {
            _currentRotationX += deltaX;
            _currentRotationY += deltaY;
        }

        /// <summary>
        /// 更新縮放
        /// </summary>
        public void UpdateZoom(float scale)
        {
            _zoomScale = Math.Clamp(scale, 0.5f, 3.0f);
        }

        /// <summary>
        /// 獲取當前視圖狀態
        /// </summary>
        public ViewState GetViewState()
        {
            return new ViewState
            {
                RotationX = _currentRotationX,
                RotationY = _currentRotationY,
                ZoomScale = _zoomScale
            };
        }

        /// <summary>
        /// 驗證深度數據
        /// </summary>
        private bool ValidateDepthData(byte[] depthData)
        {
            return depthData != null && depthData.Length >= 1024;
        }

        /// <summary>
        /// 處理深度數據
        /// </summary>
        private ProcessedDepthData ProcessDepthData(byte[] depthData)
        {
            // 將原始深度數據轉換為浮點數數組
            var floatCount = depthData.Length / 4;
            var depthValues = new float[floatCount];
            
            for (int i = 0; i < floatCount; i++)
            {
                var bytes = new byte[4];
                Array.Copy(depthData, i * 4, bytes, 0, 4);
                depthValues[i] = BitConverter.ToSingle(bytes, 0);
            }
            
            // 過濾無效值
            var validDepths = depthValues.Where(d => d > 0 && d < 1000).ToArray();
            
            return new ProcessedDepthData
            {
                DepthValues = validDepths,
                Width = 64, // 假設64x64深度圖
                Height = 64,
                MinDepth = validDepths.Length > 0 ? validDepths.Min() : 0f,
                MaxDepth = validDepths.Length > 0 ? validDepths.Max() : 0f
            };
        }

        /// <summary>
        /// 生成3D模型
        /// </summary>
        private Wound3DModelData Generate3DModel(ProcessedDepthData depthData, double woundArea)
        {
            var vertices = new List<Vector3>();
            var colors = new List<Vector4>();
            var indices = new List<int>();
            
            var width = depthData.Width;
            var height = depthData.Height;
            
            // 生成頂點
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    var index = y * width + x;
                    if (index < depthData.DepthValues.Length)
                    {
                        var depth = depthData.DepthValues[index];
                        var normalizedDepth = (depth - depthData.MinDepth) / 
                                            (depthData.MaxDepth - depthData.MinDepth);
                        
                        // 頂點位置
                        var position = new Vector3(
                            (float)x / width - 0.5f, // X
                            (float)y / height - 0.5f, // Y
                            normalizedDepth * 0.5f // Z
                        );
                        vertices.Add(position);
                        
                        // 頂點顏色 (基於深度)
                        var color = new Vector4(
                            normalizedDepth, // R
                            0.5f, // G
                            1.0f - normalizedDepth, // B
                            1.0f // A
                        );
                        colors.Add(color);
                    }
                }
            }
            
            // 生成索引 (三角形)
            for (int y = 0; y < height - 1; y++)
            {
                for (int x = 0; x < width - 1; x++)
                {
                    var topLeft = y * width + x;
                    var topRight = topLeft + 1;
                    var bottomLeft = (y + 1) * width + x;
                    var bottomRight = bottomLeft + 1;
                    
                    // 第一個三角形
                    indices.Add(topLeft);
                    indices.Add(bottomLeft);
                    indices.Add(topRight);
                    
                    // 第二個三角形
                    indices.Add(topRight);
                    indices.Add(bottomLeft);
                    indices.Add(bottomRight);
                }
            }
            
            return new Wound3DModelData
            {
                Vertices = vertices.ToArray(),
                Colors = colors.ToArray(),
                Indices = indices.ToArray(),
                VertexCount = vertices.Count,
                IndexCount = indices.Count
            };
        }

        /// <summary>
        /// 計算深度統計信息
        /// </summary>
        private DepthStatistics CalculateDepthStatistics(ProcessedDepthData depthData, double woundArea)
        {
            var depths = depthData.DepthValues;
            
            var averageDepth = depths.Average();
            var depthVariance = depths.Select(d => Math.Pow(d - averageDepth, 2)).Average();
            var depthStandardDeviation = Math.Sqrt(depthVariance);
            
            return new DepthStatistics
            {
                AverageDepth = (float)averageDepth,
                MinDepth = depthData.MinDepth,
                MaxDepth = depthData.MaxDepth,
                DepthVariance = (float)depthVariance,
                DepthStandardDeviation = (float)depthStandardDeviation,
                EstimatedVolume = CalculateEstimatedVolume(depths, woundArea),
                SurfaceRoughness = CalculateSurfaceRoughness(depths)
            };
        }

        /// <summary>
        /// 計算估算體積
        /// </summary>
        private double CalculateEstimatedVolume(float[] depths, double woundArea)
        {
            var averageDepth = depths.Average();
            return woundArea * averageDepth * 0.1; // 簡化的體積計算
        }

        /// <summary>
        /// 計算表面粗糙度
        /// </summary>
        private double CalculateSurfaceRoughness(float[] depths)
        {
            var averageDepth = depths.Average();
            var roughness = depths.Select(d => Math.Abs(d - averageDepth)).Average();
            return roughness;
        }

        /// <summary>
        /// 導出3D模型為OBJ格式
        /// </summary>
        public async Task<string> ExportToOBJAsync(Wound3DModelData modelData, string filePath)
        {
            return await Task.Run(() =>
            {
                try
                {
                    using var writer = new StreamWriter(filePath);
                    
                    // 寫入頂點
                    foreach (var vertex in modelData.Vertices)
                    {
                        writer.WriteLine($"v {vertex.X:F6} {vertex.Y:F6} {vertex.Z:F6}");
                    }
                    
                    // 寫入面
                    for (int i = 0; i < modelData.Indices.Length; i += 3)
                    {
                        var v1 = modelData.Indices[i] + 1;
                        var v2 = modelData.Indices[i + 1] + 1;
                        var v3 = modelData.Indices[i + 2] + 1;
                        writer.WriteLine($"f {v1} {v2} {v3}");
                    }
                    
                    _logger.LogInformation("3D模型已導出到: {FilePath}", filePath);
                    return filePath;
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "導出3D模型失敗");
                    throw;
                }
            });
        }

        // 事件觸發方法
        protected virtual void OnVisualizationCompleted(Wound3DVisualizationResult result)
        {
            VisualizationCompleted?.Invoke(this, result);
        }

        protected virtual void OnGenerationStatusChanged(bool isGenerating)
        {
            GenerationStatusChanged?.Invoke(this, isGenerating);
        }

        public void Dispose()
        {
            // 清理資源
        }
    }

    /// <summary>
    /// 3D視覺化結果
    /// </summary>
    public class Wound3DVisualizationResult
    {
        public Wound3DModelData? ModelData { get; set; }
        public DepthStatistics? Statistics { get; set; }
        public DateTime GenerationTime { get; set; }
        public bool Success { get; set; }
        public string? ErrorMessage { get; set; }
    }

    /// <summary>
    /// 處理後的深度數據
    /// </summary>
    public class ProcessedDepthData
    {
        public float[] DepthValues { get; set; } = Array.Empty<float>();
        public int Width { get; set; }
        public int Height { get; set; }
        public float MinDepth { get; set; }
        public float MaxDepth { get; set; }
    }

    /// <summary>
    /// 3D模型數據
    /// </summary>
    public class Wound3DModelData
    {
        public Vector3[] Vertices { get; set; } = Array.Empty<Vector3>();
        public Vector4[] Colors { get; set; } = Array.Empty<Vector4>();
        public int[] Indices { get; set; } = Array.Empty<int>();
        public int VertexCount { get; set; }
        public int IndexCount { get; set; }
    }

    /// <summary>
    /// 深度統計信息
    /// </summary>
    public class DepthStatistics
    {
        public float AverageDepth { get; set; }
        public float MinDepth { get; set; }
        public float MaxDepth { get; set; }
        public float DepthVariance { get; set; }
        public float DepthStandardDeviation { get; set; }
        public double EstimatedVolume { get; set; }
        public double SurfaceRoughness { get; set; }
    }

    /// <summary>
    /// 視圖狀態
    /// </summary>
    public class ViewState
    {
        public float RotationX { get; set; }
        public float RotationY { get; set; }
        public float ZoomScale { get; set; }
    }
} 