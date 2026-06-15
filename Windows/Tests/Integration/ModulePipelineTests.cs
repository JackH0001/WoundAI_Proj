using System.Drawing;
using System.Drawing.Imaging;
using FluentAssertions;
using WoundMeasurement.AI.Modules;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Models;
using WoundMeasurement.Measurement.Modules;
using WoundMeasurement.Processing.Modules;
using Xunit;
using CoreMeasurement = WoundMeasurement.Core.Models.WoundMeasurement;

namespace WoundMeasurement.Tests.Integration
{
    /// <summary>
    /// End-to-end integration tests wiring Processing → AI → Measurement
    /// against a synthetic wound image.
    /// </summary>
    public class ModulePipelineTests : IDisposable
    {
        private readonly Bitmap _syntheticWoundImage;
        private readonly OpenCVProcessingModule _processing;
        private readonly OnnxAIModule _ai;
        private readonly OpenCVMeasurementModule _measurement;

        public ModulePipelineTests()
        {
            _syntheticWoundImage = CreateSyntheticWound(256, 256, radius: 40);
            _processing = new OpenCVProcessingModule();
            _ai = new OnnxAIModule();
            _measurement = new OpenCVMeasurementModule();
        }

        public void Dispose()
        {
            _syntheticWoundImage.Dispose();
            _processing.Dispose();
            _ai.Dispose();
            _measurement.Dispose();
        }

        // ------------------------------------------------------------------
        // Fixtures
        // ------------------------------------------------------------------

        private static Bitmap CreateSyntheticWound(int w, int h, int radius)
        {
            var bmp = new Bitmap(w, h, PixelFormat.Format24bppRgb);
            using var g = Graphics.FromImage(bmp);
            g.Clear(Color.FromArgb(210, 170, 150));      // skin tone
            using var wound = new SolidBrush(Color.FromArgb(190, 50, 60));
            g.FillEllipse(wound, w / 2 - radius, h / 2 - radius, radius * 2, radius * 2);
            using var necrotic = new SolidBrush(Color.FromArgb(60, 30, 30));
            int r2 = radius / 3;
            g.FillEllipse(necrotic, w / 2 - r2, h / 2 - r2, r2 * 2, r2 * 2);
            return bmp;
        }

        private ImageData AsImageData(Bitmap bmp) => new ImageData
        {
            RgbImage = bmp,
            Width = bmp.Width,
            Height = bmp.Height,
            Timestamp = DateTime.Now,
            QualityScore = 80
        };

        // ------------------------------------------------------------------
        // Processing module
        // ------------------------------------------------------------------

        [Fact]
        public async Task Processing_Initialize_Succeeds()
        {
            (await _processing.InitializeAsync(new ProcessingSettings())).Should().BeTrue();
            _processing.IsInitialized.Should().BeTrue();
            _processing.ModuleName.Should().Contain("OpenCV");
        }

        [Fact]
        public async Task Processing_AssessQuality_ProducesReasonableScore()
        {
            await _processing.InitializeAsync(new ProcessingSettings());
            var qa = await _processing.AssessQualityAsync(AsImageData(_syntheticWoundImage));
            qa.Should().NotBeNull();
            qa.OverallScore.Should().BeInRange(0, 100);
            qa.BrightnessScore.Should().BeInRange(0, 100);
            qa.ContrastScore.Should().BeInRange(0, 100);
        }

        [Fact]
        public async Task Processing_DetectROI_FindsWoundRegion()
        {
            await _processing.InitializeAsync(new ProcessingSettings());
            var roi = await _processing.DetectROIAsync(AsImageData(_syntheticWoundImage));
            roi.Should().NotBe(System.Drawing.Rectangle.Empty);
            // ROI should be centred roughly in the image
            roi.X.Should().BeGreaterThan(50);
            roi.Y.Should().BeGreaterThan(50);
            roi.Width.Should().BeGreaterThan(40);
            roi.Height.Should().BeGreaterThan(40);
        }

        [Fact]
        public async Task Processing_ProcessImage_ReturnsSuccess()
        {
            await _processing.InitializeAsync(new ProcessingSettings());
            var result = await _processing.ProcessImageAsync(AsImageData(_syntheticWoundImage));
            result.IsSuccess.Should().BeTrue();
            result.ProcessedImage.RgbImage.Should().NotBeNull();
            result.ProcessingTimeMs.Should().BeGreaterThanOrEqualTo(0);
        }

        // ------------------------------------------------------------------
        // AI module
        // ------------------------------------------------------------------

        [Fact]
        public async Task AI_Initialize_SucceedsWithoutModel()
        {
            // When no ONNX file is present the module falls back to heuristic
            (await _ai.InitializeAsync(new AISettings())).Should().BeTrue();
            _ai.IsInitialized.Should().BeTrue();
        }

        [Fact]
        public async Task AI_SegmentWound_ProducesMask()
        {
            await _ai.InitializeAsync(new AISettings());
            using var mask = await _ai.SegmentWoundAsync(AsImageData(_syntheticWoundImage));
            mask.Should().NotBeNull();
            mask!.Width.Should().Be(_syntheticWoundImage.Width);
            mask.Height.Should().Be(_syntheticWoundImage.Height);
        }

        [Fact]
        public async Task AI_ClassifyWound_ReturnsValidResult()
        {
            await _ai.InitializeAsync(new AISettings());
            var cls = await _ai.ClassifyWoundAsync(AsImageData(_syntheticWoundImage));
            cls.Should().NotBeNull();
            cls.AcuteProbability.Should().BeInRange(0, 1);
            cls.ChronicProbability.Should().BeInRange(0, 1);
            cls.Confidence.Should().BeInRange(0, 1);
        }

        // ------------------------------------------------------------------
        // Measurement module
        // ------------------------------------------------------------------

        [Fact]
        public async Task Measurement_CalibratePixelSize_Updates()
        {
            await _measurement.InitializeAsync(new MeasurementSettings());
            (await _measurement.CalibratePixelSizeAsync(100.0, 500.0)).Should().BeTrue();
            // Rejected on bad input
            (await _measurement.CalibratePixelSizeAsync(-1, 1)).Should().BeFalse();
            (await _measurement.CalibratePixelSizeAsync(1, 0)).Should().BeFalse();
        }

        [Fact]
        public async Task Measurement_Area_MatchesSyntheticWound()
        {
            await _measurement.InitializeAsync(new MeasurementSettings { PixelSizeMm = 0.1 });
            using var mask = MakeBinaryMaskOfSyntheticWound();
            var areaMm2 = await _measurement.CalculateAreaAsync(mask, 0.1);
            // expected = π·r² × pixelArea ≈ π·40² × 0.01 ≈ 50.27 mm²
            areaMm2.Should().BeInRange(40, 65);
        }

        [Fact]
        public async Task Measurement_Perimeter_MatchesSyntheticWound()
        {
            await _measurement.InitializeAsync(new MeasurementSettings { PixelSizeMm = 0.1 });
            using var mask = MakeBinaryMaskOfSyntheticWound();
            var perimMm = await _measurement.CalculatePerimeterAsync(mask, 0.1);
            // expected = 2πr × pixelSize ≈ 2π·40 × 0.1 ≈ 25.13 mm
            perimMm.Should().BeInRange(20, 35);
        }

        [Fact]
        public async Task Measurement_ExtractBoundary_ReturnsPoints()
        {
            await _measurement.InitializeAsync(new MeasurementSettings());
            using var mask = MakeBinaryMaskOfSyntheticWound();
            var pts = await _measurement.ExtractBoundaryAsync(mask);
            pts.Length.Should().BeGreaterThan(50);
        }

        // ------------------------------------------------------------------
        // Full pipeline
        // ------------------------------------------------------------------

        [Fact]
        public async Task FullPipeline_Processing_AI_Measurement_Integrates()
        {
            await _processing.InitializeAsync(new ProcessingSettings());
            await _ai.InitializeAsync(new AISettings());
            await _measurement.InitializeAsync(new MeasurementSettings { PixelSizeMm = 0.1 });

            var input = AsImageData(_syntheticWoundImage);
            var processed = await _processing.ProcessImageAsync(input);
            processed.IsSuccess.Should().BeTrue();

            using var mask = await _ai.SegmentWoundAsync(processed.ProcessedImage);
            mask.Should().NotBeNull();

            CoreMeasurement measurement = await _measurement.MeasureWoundAsync(
                processed.ProcessedImage, mask!);
            measurement.Should().NotBeNull();
            measurement.Area.Should().BeGreaterThanOrEqualTo(0);
            measurement.Perimeter.Should().BeGreaterThanOrEqualTo(0);
        }

        // ------------------------------------------------------------------
        // Helpers
        // ------------------------------------------------------------------

        private Bitmap MakeBinaryMaskOfSyntheticWound()
        {
            int w = _syntheticWoundImage.Width;
            int h = _syntheticWoundImage.Height;
            var mask = new Bitmap(w, h, PixelFormat.Format24bppRgb);
            using var g = Graphics.FromImage(mask);
            g.Clear(Color.Black);
            using var white = new SolidBrush(Color.White);
            g.FillEllipse(white, w / 2 - 40, h / 2 - 40, 80, 80);
            return mask;
        }
    }
}
