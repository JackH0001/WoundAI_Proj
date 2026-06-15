using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Threading.Tasks;
using OpenCvSharp;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace WoundMeasurement.Capture.Modules
{
    public class RulerCalibrationModule : INotifyPropertyChanged
    {
        private bool _isCalibrating = false;
        private CalibrationResult _calibrationResult = null;

        public bool IsCalibrating
        {
            get => _isCalibrating;
            private set
            {
                _isCalibrating = value;
                OnPropertyChanged();
            }
        }

        public CalibrationResult CalibrationResult
        {
            get => _calibrationResult;
            private set
            {
                _calibrationResult = value;
                OnPropertyChanged();
            }
        }

        public event PropertyChangedEventHandler PropertyChanged;

        protected virtual void OnPropertyChanged([CallerMemberName] string propertyName = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        }

        public async Task<CalibrationResult> DetectAndCalibrateRulerAsync(Mat image)
        {
            IsCalibrating = true;
            
            try
            {
                var gridPattern = await DetectGridPatternAsync(image);
                
                var colorCorners = await DetectColorCornersAsync(image);
                
                var correctionResult = PerformPerspectiveCorrection(image, colorCorners);
                
                var pixelScale = CalculatePixelScale(correctionResult.CorrectedImage);
                
                var confidence = CalculateConfidence(gridPattern, colorCorners);
                
                var result = new CalibrationResult
                {
                    PixelPerMM = pixelScale,
                    TransformMatrix = correctionResult.TransformMatrix,
                    Confidence = confidence,
                    GridPattern = gridPattern,
                    DetectedCorners = colorCorners,
                    CorrectedRulerImage = correctionResult.CorrectedImage.Clone()
                };
                
                CalibrationResult = result;
                return result;
            }
            catch (Exception ex)
            {
                throw new CalibrationException($"標尺校正失敗: {ex.Message}", ex);
            }
            finally
            {
                IsCalibrating = false;
            }
        }

        private async Task<GridPattern> DetectGridPatternAsync(Mat image)
        {
            return await Task.Run(() =>
            {
                using var gray = new Mat();
                using var edges = new Mat();
                using var hierarchy = new Mat();
                
                Cv2.CvtColor(image, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.GaussianBlur(gray, gray, new OpenCvSharp.Size(5, 5), 0);
                Cv2.Canny(gray, edges, 50, 150);
                
                // 檢測輪廓
                Cv2.FindContours(edges, out var contours, hierarchy, RetrievalModes.Tree, ContourApproximationModes.ApproxSimple);
                
                var rectangularContours = new List<Mat>();
                
                foreach (var contour in contours)
                {
                    var epsilon = 0.02 * Cv2.ArcLength(contour, true);
                    using var approx = new Mat();
                    Cv2.ApproxPolyDP(contour, approx, epsilon, true);
                    
                    if (approx.Rows == 4)
                    {
                        var area = Cv2.ContourArea(contour);
                        if (area > 100 && area < image.Width * image.Height * 0.1)
                        {
                            rectangularContours.Add(contour.Clone());
                        }
                    }
                }
                
                // 分析網格模式
                var horizontalLines = new List<float>();
                var verticalLines = new List<float>();
                var gridCells = new List<GridCell>();
                
                foreach (var contour in rectangularContours)
                {
                    var boundingRect = Cv2.BoundingRect(contour);
                    
                    horizontalLines.Add(boundingRect.Top);
                    horizontalLines.Add(boundingRect.Bottom);
                    verticalLines.Add(boundingRect.Left);
                    verticalLines.Add(boundingRect.Right);
                    
                    gridCells.Add(new GridCell
                    {
                        TopLeft = new Point2f(boundingRect.Left, boundingRect.Top),
                        TopRight = new Point2f(boundingRect.Right, boundingRect.Top),
                        BottomLeft = new Point2f(boundingRect.Left, boundingRect.Bottom),
                        BottomRight = new Point2f(boundingRect.Right, boundingRect.Bottom)
                    });
                }
                
                // 清理重複值
                horizontalLines = horizontalLines.Distinct().OrderBy(x => x).ToList();
                verticalLines = verticalLines.Distinct().OrderBy(x => x).ToList();
                
                // 清理輪廓
                foreach (var contour in rectangularContours)
                    contour.Dispose();
                
                return new GridPattern
                {
                    HorizontalLines = horizontalLines,
                    VerticalLines = verticalLines,
                    GridCells = gridCells,
                    Confidence = Math.Min(1.0f, gridCells.Count / 25.0f)
                };
            });
        }

        private async Task<List<ColorCorner>> DetectColorCornersAsync(Mat image)
        {
            return await Task.Run(() =>
            {
                using var gray = new Mat();
                using var edges = new Mat();
                using var hierarchy = new Mat();
                
                Cv2.CvtColor(image, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.Canny(gray, edges, 50, 150);
                
                // 檢測最大矩形區域
                Cv2.FindContours(edges, out var contours, hierarchy, RetrievalModes.External, ContourApproximationModes.ApproxSimple);
                
                Mat largestContour = null;
                double maxArea = 0;
                
                foreach (var contour in contours)
                {
                    var area = Cv2.ContourArea(contour);
                    if (area > maxArea && area > image.Width * image.Height * 0.1)
                    {
                        largestContour?.Dispose();
                        largestContour = contour.Clone();
                        maxArea = area;
                    }
                }
                
                if (largestContour == null)
                {
                    // 清理資源
                    foreach (var contour in contours)
                        contour.Dispose();
                    throw new CalibrationException("無法檢測到標尺區域");
                }
                
                // 獲取矩形角點
                var epsilon = 0.02 * Cv2.ArcLength(largestContour, true);
                using var approx = new Mat();
                Cv2.ApproxPolyDP(largestContour, approx, epsilon, true);
                
                var corners = new List<ColorCorner>();
                
                if (approx.Rows >= 4)
                {
                    // OpenCvSharp 4.8 沒有 Mat.ToArray<T>() generic 方法；
                    // 改用 GetArray(out T[]) 或手動轉 — 這裡用 GetArray
                    approx.GetArray(out Point2f[] points);
                    var orderedPoints = OrderPoints(points);
                    
                    var expectedColors = new[] { CornerColor.Red, CornerColor.Blue, CornerColor.Green, CornerColor.Yellow };
                    
                    for (int i = 0; i < Math.Min(4, orderedPoints.Length); i++)
                    {
                        var detectedColor = AnalyzeCornerColor(image, orderedPoints[i]);
                        var confidence = CalculateColorConfidence(expectedColors[i], detectedColor);
                        
                        corners.Add(new ColorCorner
                        {
                            Position = orderedPoints[i],
                            ExpectedColor = expectedColors[i],
                            DetectedColor = detectedColor,
                            Confidence = confidence
                        });
                    }
                }
                
                // 清理資源
                foreach (var contour in contours)
                    contour.Dispose();
                largestContour?.Dispose();
                
                return corners;
            });
        }

        private Point2f[] OrderPoints(Point2f[] points)
        {
            if (points.Length < 4) return points;
            
            // 按 Y 座標排序，然後按 X 座標排序
            var ordered = new Point2f[4];
            var sorted = points.OrderBy(p => p.Y).ThenBy(p => p.X).ToArray();
            
            // Top-left, Top-right (Y值較小的兩個點)
            if (sorted[0].X < sorted[1].X)
            {
                ordered[0] = sorted[0]; // top-left (red)
                ordered[1] = sorted[1]; // top-right (blue)
            }
            else
            {
                ordered[0] = sorted[1]; // top-left (red)
                ordered[1] = sorted[0]; // top-right (blue)
            }
            
            // Bottom-left, Bottom-right (Y值較大的兩個點)
            if (sorted[2].X < sorted[3].X)
            {
                ordered[3] = sorted[2]; // bottom-left (yellow)
                ordered[2] = sorted[3]; // bottom-right (green)
            }
            else
            {
                ordered[3] = sorted[3]; // bottom-left (yellow)
                ordered[2] = sorted[2]; // bottom-right (green)
            }
            
            return ordered;
        }

        private CornerColor AnalyzeCornerColor(Mat image, Point2f cornerPoint)
        {
            var x = Math.Max(0, Math.Min(image.Width - 1, (int)cornerPoint.X));
            var y = Math.Max(0, Math.Min(image.Height - 1, (int)cornerPoint.Y));
            
            // 取周圍10x10區域的平均顏色
            var regionSize = 10;
            var startX = Math.Max(0, x - regionSize / 2);
            var startY = Math.Max(0, y - regionSize / 2);
            var endX = Math.Min(image.Width, x + regionSize / 2);
            var endY = Math.Min(image.Height, y + regionSize / 2);
            
            var roi = new Rect(startX, startY, endX - startX, endY - startY);
            using var regionImage = new Mat(image, roi);
            
            var mean = Cv2.Mean(regionImage);
            var b = mean[0]; // Blue
            var g = mean[1]; // Green  
            var r = mean[2]; // Red
            
            // 判斷顏色 (BGR格式)
            if (r > g && r > b && r > 150)
                return CornerColor.Red;
            else if (b > g && b > r && b > 150)
                return CornerColor.Blue;
            else if (g > r && g > b && g > 150)
                return CornerColor.Green;
            else if (r > 200 && g > 200 && b < 100)
                return CornerColor.Yellow;
            else
                return CornerColor.Unknown;
        }

        private float CalculateColorConfidence(CornerColor expected, CornerColor detected)
        {
            return expected == detected ? 1.0f : 0.3f;
        }

        private (Mat CorrectedImage, Mat TransformMatrix) PerformPerspectiveCorrection(Mat image, List<ColorCorner> corners)
        {
            if (corners.Count < 4)
                throw new CalibrationException("角點數量不足，無法進行透視校正");
            
            var sourcePoints = corners.Select(c => c.Position).ToArray();
            
            var targetSize = 300f;
            var targetPoints = new Point2f[]
            {
                new Point2f(0, 0),                    // top-left
                new Point2f(targetSize, 0),           // top-right
                new Point2f(targetSize, targetSize),  // bottom-right
                new Point2f(0, targetSize)            // bottom-left
            };
            
            var transformMatrix = Cv2.GetPerspectiveTransform(sourcePoints, targetPoints);
            var correctedImage = new Mat();
            
            Cv2.WarpPerspective(image, correctedImage, transformMatrix, new OpenCvSharp.Size(targetSize, targetSize));
            
            return (correctedImage, transformMatrix);
        }

        private double CalculatePixelScale(Mat correctedImage)
        {
            var gridSpacing = AnalyzeGridSpacing(correctedImage);
            
            if (gridSpacing.HorizontalSpacing <= 0 || gridSpacing.VerticalSpacing <= 0)
                throw new CalibrationException("無法計算網格間距");
            
            var averageSpacing = (gridSpacing.HorizontalSpacing + gridSpacing.VerticalSpacing) / 2.0;
            
            // 30mm標尺 / 網格數量，假設300像素 = 30mm
            var pixelPerMM = averageSpacing / 10.0; // 每10像素 = 1mm
            
            return pixelPerMM;
        }

        private GridSpacingAnalysis AnalyzeGridSpacing(Mat image)
        {
            using var gray = new Mat();
            Cv2.CvtColor(image, gray, ColorConversionCodes.BGR2GRAY);
            
            var centerY = image.Height / 2;
            var centerX = image.Width / 2;
            
            var horizontalSpacings = new List<double>();
            var verticalSpacings = new List<double>();
            
            // 檢測水平線
            var lastLineX = -1;
            for (int x = 0; x < image.Width; x += 2)
            {
                if (IsGridLine(gray, x, centerY))
                {
                    if (lastLineX >= 0)
                    {
                        horizontalSpacings.Add(x - lastLineX);
                    }
                    lastLineX = x;
                }
            }
            
            // 檢測垂直線
            var lastLineY = -1;
            for (int y = 0; y < image.Height; y += 2)
            {
                if (IsGridLine(gray, centerX, y))
                {
                    if (lastLineY >= 0)
                    {
                        verticalSpacings.Add(y - lastLineY);
                    }
                    lastLineY = y;
                }
            }
            
            var avgHorizontal = horizontalSpacings.Count > 0 ? horizontalSpacings.Average() : 0;
            var avgVertical = verticalSpacings.Count > 0 ? verticalSpacings.Average() : 0;
            
            return new GridSpacingAnalysis
            {
                HorizontalSpacing = avgHorizontal,
                VerticalSpacing = avgVertical
            };
        }

        private bool IsGridLine(Mat grayImage, int x, int y)
        {
            if (x < 0 || x >= grayImage.Width || y < 0 || y >= grayImage.Height)
                return false;
            
            var pixel = grayImage.At<byte>(y, x);
            return pixel < 100; // 深色像素表示線條
        }

        private float CalculateConfidence(GridPattern gridPattern, List<ColorCorner> corners)
        {
            var gridConfidence = gridPattern.Confidence;
            var cornerConfidence = corners.Count > 0 ? corners.Average(c => c.Confidence) : 0f;
            
            return (gridConfidence + cornerConfidence) / 2.0f;
        }

        public void Dispose()
        {
            CalibrationResult?.CorrectedRulerImage?.Dispose();
        }
    }

    public class CalibrationResult
    {
        public double PixelPerMM { get; set; }
        public Mat TransformMatrix { get; set; }
        public float Confidence { get; set; }
        public GridPattern GridPattern { get; set; }
        public List<ColorCorner> DetectedCorners { get; set; }
        public Mat CorrectedRulerImage { get; set; }
        
        public bool IsReliable => Confidence >= 0.8f && PixelPerMM > 5.0 && PixelPerMM < 20.0;
        
        public string AccuracyEstimate
        {
            get
            {
                if (Confidence >= 0.95f) return "±2-3%";
                if (Confidence >= 0.85f) return "±3-5%";
                if (Confidence >= 0.7f) return "±5-8%";
                return "±8-15%";
            }
        }
    }

    public class GridPattern
    {
        public List<float> HorizontalLines { get; set; } = new List<float>();
        public List<float> VerticalLines { get; set; } = new List<float>();
        public List<GridCell> GridCells { get; set; } = new List<GridCell>();
        public float Confidence { get; set; }
    }

    public class GridCell
    {
        public Point2f TopLeft { get; set; }
        public Point2f TopRight { get; set; }
        public Point2f BottomLeft { get; set; }
        public Point2f BottomRight { get; set; }
    }

    public class ColorCorner
    {
        public Point2f Position { get; set; }
        public CornerColor ExpectedColor { get; set; }
        public CornerColor DetectedColor { get; set; }
        public float Confidence { get; set; }
    }

    public enum CornerColor
    {
        Red, Blue, Green, Yellow, Unknown
    }

    public class GridSpacingAnalysis
    {
        public double HorizontalSpacing { get; set; }
        public double VerticalSpacing { get; set; }
    }

    public class CalibrationException : Exception
    {
        public CalibrationException(string message) : base(message) { }
        public CalibrationException(string message, Exception innerException) : base(message, innerException) { }
    }
}