using System.Drawing;
using Microsoft.Extensions.Logging;
using OpenCvSharp;
using OpenCvSharp.Extensions;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Processing.Modules
{
    /// <summary>
    /// Real OpenCV-based processing module.  Performs white-balance, gamma,
    /// noise reduction, optional edge enhancement, a Laplacian-variance based
    /// quality metric, and a colour-based ROI detector.
    /// </summary>
    public class OpenCVProcessingModule : IProcessingModule
    {
        private readonly ILogger<OpenCVProcessingModule>? _logger;
        private ProcessingSettings _settings = new();

        public string ModuleName => "OpenCV Processing Module";
        public bool IsInitialized { get; private set; }

        public OpenCVProcessingModule(ILogger<OpenCVProcessingModule>? logger = null)
        {
            _logger = logger;
        }

        public Task<bool> InitializeAsync(ProcessingSettings settings)
        {
            _settings = settings ?? new ProcessingSettings();
            IsInitialized = true;
            _logger?.LogInformation("OpenCVProcessingModule initialised");
            return Task.FromResult(true);
        }

        public Task<ProcessingResult> ProcessImageAsync(ImageData inputImage)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var result = new ProcessingResult
            {
                OriginalImage = inputImage,
                IsSuccess = false
            };
            try
            {
                if (inputImage?.RgbImage == null)
                {
                    result.ErrorMessage = "Input image is null";
                    return Task.FromResult(result);
                }

                using var src = BitmapConverter.ToMat(inputImage.RgbImage);
                using var bgr = new Mat();
                Cv2.CvtColor(src, bgr, ColorConversionCodes.BGRA2BGR);

                using var processed = bgr.Clone();
                if (_settings.EnableWhiteBalance) ApplyWhiteBalance(processed);
                if (_settings.EnableGammaCorrection) ApplyGamma(processed, _settings.GammaValue);
                if (_settings.EnableNoiseReduction)
                    Cv2.FastNlMeansDenoisingColored(processed, processed, (float)(10 * _settings.NoiseReductionStrength));
                if (_settings.EnableEdgeEnhancement) ApplyEdgeEnhancement(processed, _settings.EdgeEnhancementStrength);

                var processedBitmap = BitmapConverter.ToBitmap(processed);
                result.ProcessedImage = new ImageData
                {
                    RgbImage = processedBitmap,
                    Width = processedBitmap.Width,
                    Height = processedBitmap.Height,
                    Timestamp = DateTime.Now,
                    QualityScore = inputImage.QualityScore
                };
                result.IsSuccess = true;
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "ProcessImage failed");
                result.ErrorMessage = ex.Message;
            }
            finally
            {
                sw.Stop();
                result.ProcessingTimeMs = sw.ElapsedMilliseconds;
            }
            return Task.FromResult(result);
        }

        public Task<QualityAssessment> AssessQualityAsync(ImageData image)
        {
            var qa = new QualityAssessment();
            if (image?.RgbImage == null)
            {
                qa.QualityIssues.Add("No image");
                return Task.FromResult(qa);
            }
            try
            {
                using var src = BitmapConverter.ToMat(image.RgbImage);
                using var gray = new Mat();
                Cv2.CvtColor(src, gray, ColorConversionCodes.BGR2GRAY);

                // Laplacian variance -> sharpness / motion blur
                using var lap = new Mat();
                Cv2.Laplacian(gray, lap, MatType.CV_64F);
                Cv2.MeanStdDev(lap, out _, out var stdev);
                double sharpness = stdev.Val0 * stdev.Val0;
                qa.MotionBlur = Math.Clamp(1.0 - Math.Min(sharpness / 500.0, 1.0), 0, 1);

                // Brightness & contrast
                Cv2.MeanStdDev(gray, out var mean, out var std);
                qa.BrightnessScore = Math.Clamp(mean.Val0 / 255.0 * 100.0, 0, 100);
                qa.ContrastScore = Math.Clamp(std.Val0 / 128.0 * 100.0, 0, 100);

                // SNR (rough)
                qa.SignalToNoiseRatio = std.Val0 > 0 ? 20.0 * Math.Log10(mean.Val0 / Math.Max(1.0, std.Val0)) : 0;

                // Depth coverage / confidence from ImageData if supplied
                qa.DepthCoverage = image.DepthMetrics?.Coverage ?? 1.0;
                qa.AverageConfidence = 0.8;

                qa.OverallScore = Math.Clamp(
                    0.35 * qa.BrightnessScore +
                    0.25 * qa.ContrastScore +
                    0.25 * (1.0 - qa.MotionBlur) * 100.0 +
                    0.15 * qa.DepthCoverage * 100.0,
                    0, 100);

                qa.PassesQualityCheck = qa.OverallScore >= _settings.MinQualityScore &&
                                         qa.DepthCoverage >= _settings.MinDepthCoverage;
                if (qa.MotionBlur > 0.5) qa.QualityIssues.Add("影像模糊");
                if (qa.BrightnessScore < 20) qa.QualityIssues.Add("影像過暗");
                if (qa.BrightnessScore > 90) qa.QualityIssues.Add("影像過亮");
                if (qa.ContrastScore < 15) qa.QualityIssues.Add("對比度過低");
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Quality assessment failed");
                qa.QualityIssues.Add(ex.Message);
            }
            return Task.FromResult(qa);
        }

        public Task<Rectangle> DetectROIAsync(ImageData image)
        {
            if (image?.RgbImage == null)
                return Task.FromResult(Rectangle.Empty);
            try
            {
                using var src = BitmapConverter.ToMat(image.RgbImage);
                using var hsv = new Mat();
                Cv2.CvtColor(src, hsv, ColorConversionCodes.BGR2HSV);

                using var m1 = new Mat();
                using var m2 = new Mat();
                // S 下限 100：膚色(如 RGB 210,170,150 → H≈10,S≈73)會落入紅色帶造成全圖誤判；
                // 傷口紅 S≈188 / 壞死 S≈128 遠高於膚色，以飽和度分離。
                Cv2.InRange(hsv, new Scalar(0, 100, 40), new Scalar(15, 255, 255), m1);
                Cv2.InRange(hsv, new Scalar(160, 100, 40), new Scalar(180, 255, 255), m2);
                using var mask = new Mat();
                Cv2.BitwiseOr(m1, m2, mask);
                using var kernel = Cv2.GetStructuringElement(MorphShapes.Ellipse, new OpenCvSharp.Size(7, 7));
                Cv2.MorphologyEx(mask, mask, MorphTypes.Close, kernel, iterations: 2);

                Cv2.FindContours(mask, out var contours, out _, RetrievalModes.External, ContourApproximationModes.ApproxSimple);
                if (contours.Length == 0)
                    return Task.FromResult(new Rectangle(0, 0, src.Cols, src.Rows));

                var best = contours.OrderByDescending(c => Cv2.ContourArea(c)).First();
                var r = Cv2.BoundingRect(best);
                return Task.FromResult(new Rectangle(r.X, r.Y, r.Width, r.Height));
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "DetectROI failed; returning full image rect");
                return Task.FromResult(new Rectangle(0, 0, image.Width, image.Height));
            }
        }

        public void Dispose()
        {
            IsInitialized = false;
        }

        // ---- helpers -----------------------------------------------------

        private static void ApplyWhiteBalance(Mat bgr)
        {
            // Simple gray-world white balance
            Cv2.Split(bgr, out var channels);
            try
            {
                double mb = Cv2.Mean(channels[0]).Val0;
                double mg = Cv2.Mean(channels[1]).Val0;
                double mr = Cv2.Mean(channels[2]).Val0;
                double avg = (mb + mg + mr) / 3.0;
                if (mb > 1e-3) channels[0].ConvertTo(channels[0], MatType.CV_8U, avg / mb);
                if (mg > 1e-3) channels[1].ConvertTo(channels[1], MatType.CV_8U, avg / mg);
                if (mr > 1e-3) channels[2].ConvertTo(channels[2], MatType.CV_8U, avg / mr);
                Cv2.Merge(channels, bgr);
            }
            finally
            {
                foreach (var c in channels) c.Dispose();
            }
        }

        private static void ApplyGamma(Mat bgr, double gamma)
        {
            if (Math.Abs(gamma - 1.0) < 1e-3) return;
            var lut = new byte[256];
            double invG = 1.0 / Math.Max(gamma, 0.1);
            for (int i = 0; i < 256; i++)
                lut[i] = (byte)Math.Clamp(Math.Pow(i / 255.0, invG) * 255.0, 0, 255);
            using var lutMat = new Mat(1, 256, MatType.CV_8U, lut);
            Cv2.LUT(bgr, lutMat, bgr);
        }

        private static void ApplyEdgeEnhancement(Mat bgr, double strength)
        {
            using var blur = new Mat();
            Cv2.GaussianBlur(bgr, blur, new OpenCvSharp.Size(0, 0), 3);
            Cv2.AddWeighted(bgr, 1.0 + strength, blur, -strength, 0, bgr);
        }
    }
}
