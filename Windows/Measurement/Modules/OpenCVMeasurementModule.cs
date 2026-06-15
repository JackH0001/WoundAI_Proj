using System.Drawing;
using Microsoft.Extensions.Logging;
using OpenCvSharp;
using OpenCvSharp.Extensions;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;
using CoreMeasurement = WoundMeasurement.Core.Models.WoundMeasurement;

// alias 解 System.Drawing.Size vs OpenCvSharp.Size 衝突
using Size = System.Drawing.Size;

namespace WoundMeasurement.Measurement.Modules
{
    /// <summary>
    /// OpenCV-based wound measurement module.  Computes area, perimeter,
    /// length/width, depth and volume from a binary wound mask.
    /// </summary>
    public class OpenCVMeasurementModule : IMeasurementModule
    {
        private readonly ILogger<OpenCVMeasurementModule>? _logger;
        private MeasurementSettings _settings = new();
        private double _pixelSizeMm = 0.1;

        public string ModuleName => "OpenCV Measurement Module";
        public bool IsInitialized { get; private set; }

        public OpenCVMeasurementModule(ILogger<OpenCVMeasurementModule>? logger = null) => _logger = logger;

        public Task<bool> InitializeAsync(MeasurementSettings settings)
        {
            _settings = settings ?? new MeasurementSettings();
            _pixelSizeMm = _settings.PixelSizeMm > 0 ? _settings.PixelSizeMm : 0.1;
            IsInitialized = true;
            return Task.FromResult(true);
        }

        public async Task<CoreMeasurement> MeasureWoundAsync(ImageData image, Bitmap mask)
        {
            var m = new CoreMeasurement { Timestamp = DateTime.Now, MaskImage = mask };
            if (mask == null) return m;
            m.Area = await CalculateAreaAsync(mask, _pixelSizeMm);
            m.Perimeter = await CalculatePerimeterAsync(mask, _pixelSizeMm);
            if (image?.DepthMap != null)
            {
                m.Depth = await CalculateDepthAsync(image.DepthMap, mask);
                m.Volume = await CalculateVolumeAsync(image.DepthMap, mask, _pixelSizeMm);
            }
            m.BoundaryPoints = await ExtractBoundaryAsync(mask);
            return m;
        }

        public Task<double> CalculateAreaAsync(Bitmap mask, double pixelSizeMm)
        {
            try
            {
                using var m = BitmapConverter.ToMat(mask);
                using var gray = new Mat();
                Cv2.CvtColor(m, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.Threshold(gray, gray, 127, 255, ThresholdTypes.Binary);
                var px = Cv2.CountNonZero(gray);
                double areaMm2 = px * pixelSizeMm * pixelSizeMm;
                return Task.FromResult(areaMm2);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "CalculateArea failed");
                return Task.FromResult(0.0);
            }
        }

        public Task<double> CalculatePerimeterAsync(Bitmap mask, double pixelSizeMm)
        {
            try
            {
                using var m = BitmapConverter.ToMat(mask);
                using var gray = new Mat();
                Cv2.CvtColor(m, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.Threshold(gray, gray, 127, 255, ThresholdTypes.Binary);
                Cv2.FindContours(gray, out var contours, out _, RetrievalModes.External, ContourApproximationModes.ApproxNone);
                if (contours.Length == 0) return Task.FromResult(0.0);
                var largest = contours.OrderByDescending(c => Cv2.ContourArea(c)).First();
                double perimPx = Cv2.ArcLength(largest, true);
                return Task.FromResult(perimPx * pixelSizeMm);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "CalculatePerimeter failed");
                return Task.FromResult(0.0);
            }
        }

        public Task<double> CalculateDepthAsync(float[,] depthMap, Bitmap mask)
        {
            try
            {
                int h = depthMap.GetLength(0);
                int w = depthMap.GetLength(1);
                using var scaled = new Bitmap(mask, new Size(w, h));
                var rect = new Rectangle(0, 0, w, h);
                var data = scaled.LockBits(rect, System.Drawing.Imaging.ImageLockMode.ReadOnly,
                    System.Drawing.Imaging.PixelFormat.Format24bppRgb);
                double sum = 0, max = 0;
                int count = 0;
                try
                {
                    unsafe
                    {
                        byte* p = (byte*)data.Scan0;
                        int stride = data.Stride;
                        // Outside reference = mean of non-masked valid depth for baseline
                        double bgSum = 0; int bgCount = 0;
                        for (int y = 0; y < h; y++)
                            for (int x = 0; x < w; x++)
                            {
                                byte mv = p[y * stride + x * 3];
                                float d = depthMap[y, x];
                                if (float.IsNaN(d) || d <= 0) continue;
                                if (mv <= 127) { bgSum += d; bgCount++; }
                            }
                        double baseline = bgCount > 0 ? bgSum / bgCount : 0;
                        for (int y = 0; y < h; y++)
                            for (int x = 0; x < w; x++)
                            {
                                byte mv = p[y * stride + x * 3];
                                float d = depthMap[y, x];
                                if (mv > 127 && !float.IsNaN(d) && d > 0)
                                {
                                    double diff = Math.Max(0, baseline - d);
                                    sum += diff;
                                    max = Math.Max(max, diff);
                                    count++;
                                }
                            }
                    }
                }
                finally { scaled.UnlockBits(data); }

                if (count == 0) return Task.FromResult(0.0);
                // depthMap is assumed to be metres; convert to mm
                double meanDepthMm = (sum / count) * 1000.0;
                return Task.FromResult(meanDepthMm);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "CalculateDepth failed");
                return Task.FromResult(0.0);
            }
        }

        public async Task<double> CalculateVolumeAsync(float[,] depthMap, Bitmap mask, double pixelSizeMm)
        {
            double areaMm2 = await CalculateAreaAsync(mask, pixelSizeMm);
            double depthMm = await CalculateDepthAsync(depthMap, mask);
            return areaMm2 * depthMm;
        }

        public Task<System.Drawing.Point[]> ExtractBoundaryAsync(Bitmap mask)
        {
            try
            {
                using var m = BitmapConverter.ToMat(mask);
                using var gray = new Mat();
                Cv2.CvtColor(m, gray, ColorConversionCodes.BGR2GRAY);
                Cv2.Threshold(gray, gray, 127, 255, ThresholdTypes.Binary);
                Cv2.FindContours(gray, out var contours, out _, RetrievalModes.External, ContourApproximationModes.ApproxSimple);
                if (contours.Length == 0) return Task.FromResult(Array.Empty<System.Drawing.Point>());
                var largest = contours.OrderByDescending(c => Cv2.ContourArea(c)).First();
                var pts = largest.Select(p => new System.Drawing.Point(p.X, p.Y)).ToArray();
                return Task.FromResult(pts);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "ExtractBoundary failed");
                return Task.FromResult(Array.Empty<System.Drawing.Point>());
            }
        }

        public Task<bool> CalibratePixelSizeAsync(double referenceObjectSizeMm, double referenceObjectPixels)
        {
            if (referenceObjectPixels <= 0 || referenceObjectSizeMm <= 0)
                return Task.FromResult(false);
            _pixelSizeMm = referenceObjectSizeMm / referenceObjectPixels;
            _settings.PixelSizeMm = _pixelSizeMm;
            _logger?.LogInformation("Calibrated pixel size: {Size} mm/px", _pixelSizeMm);
            return Task.FromResult(true);
        }

        public void Dispose() => IsInitialized = false;
    }
}
