using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Services
{
    // ── DTOs ──────────────────────────────────────────────────────────────────

    public record BatchProcessingConfig(
        int    MaxConcurrency   = 2,
        bool   SkipLowQuality   = true,
        double MinQualityScore  = 60.0,
        string? OutputDirectory = null);

    public record BatchItemResult(
        string    FilePath,
        bool      Success,
        AnalysisResponse? Analysis,
        QualityResponse?  Quality,
        string?   ErrorMessage,
        TimeSpan  ProcessingTime);

    public class BatchProcessingProgress
    {
        public int    Total     { get; set; }
        public int    Processed { get; set; }
        public int    Failed    { get; set; }
        public string CurrentFile { get; set; } = "";
        public double ProgressPct  => Total == 0 ? 0 : (double)Processed / Total * 100;
    }

    // ── Service ───────────────────────────────────────────────────────────────

    /// <summary>
    /// 批次影像處理服務：依序對多張傷口影像執行品質評估 → AI 分析。
    /// 對應 iOS BatchProcessingService。
    /// </summary>
    public class BatchProcessingService
    {
        private readonly CloudAPIService          _api;
        private readonly ILogger<BatchProcessingService> _logger;
        private readonly SemaphoreSlim            _throttle;

        public BatchProcessingService(
            CloudAPIService api,
            ILogger<BatchProcessingService> logger)
        {
            _api      = api;
            _logger   = logger;
            _throttle = new SemaphoreSlim(1, 1);   // 預設序列處理，避免 API 過載
        }

        // ── 主要批次方法 ──────────────────────────────────────────────────────

        /// <summary>
        /// 批次處理目錄下所有影像。
        /// </summary>
        public async Task<List<BatchItemResult>> ProcessDirectoryAsync(
            string                     imageDirectory,
            BatchProcessingConfig?     config   = null,
            IProgress<BatchProcessingProgress>? progress = null,
            CancellationToken          ct       = default)
        {
            var files = Directory.GetFiles(imageDirectory, "*.*")
                .Where(f => IsImageFile(f))
                .ToList();

            return await ProcessFilesAsync(files, config, progress, ct);
        }

        /// <summary>
        /// 批次處理指定檔案清單。
        /// </summary>
        public async Task<List<BatchItemResult>> ProcessFilesAsync(
            IReadOnlyList<string>       filePaths,
            BatchProcessingConfig?      config   = null,
            IProgress<BatchProcessingProgress>? progress = null,
            CancellationToken           ct       = default)
        {
            config ??= new BatchProcessingConfig();
            var concurrency = Math.Max(1, config.MaxConcurrency);
            var sem         = new SemaphoreSlim(concurrency, concurrency);

            var prog = new BatchProcessingProgress { Total = filePaths.Count };
            var results = new List<BatchItemResult>(filePaths.Count);
            var resultLock = new object();

            var tasks = filePaths.Select(async filePath =>
            {
                await sem.WaitAsync(ct);
                try
                {
                    prog.CurrentFile = Path.GetFileName(filePath);
                    progress?.Report(prog);

                    var result = await ProcessSingleFileAsync(filePath, config, ct);

                    lock (resultLock)
                    {
                        results.Add(result);
                        prog.Processed++;
                        if (!result.Success) prog.Failed++;
                        progress?.Report(prog);
                    }
                }
                finally { sem.Release(); }
            });

            await Task.WhenAll(tasks);
            _logger.LogInformation(
                "批次完成：共 {Total} 筆，成功 {Success}，失敗 {Failed}",
                prog.Total, prog.Processed - prog.Failed, prog.Failed);

            return results.OrderBy(r => r.FilePath).ToList();
        }

        // ── 單檔處理 ──────────────────────────────────────────────────────────

        private async Task<BatchItemResult> ProcessSingleFileAsync(
            string                filePath,
            BatchProcessingConfig config,
            CancellationToken     ct)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            try
            {
                var bytes       = await File.ReadAllBytesAsync(filePath, ct);
                var fileName    = Path.GetFileName(filePath);
                var contentType = GetContentType(filePath);

                // 1. 品質評估
                QualityResponse? quality = null;
                try
                {
                    quality = await _api.AssessQualityAsync(bytes, fileName, contentType, ct);
                    if (config.SkipLowQuality && quality.OverallScore < config.MinQualityScore)
                    {
                        return new BatchItemResult(filePath, false, null, quality,
                            $"品質不足 ({quality.OverallScore:F1} < {config.MinQualityScore})",
                            sw.Elapsed);
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogWarning("品質評估失敗 {File}：{Msg}", fileName, ex.Message);
                }

                // 2. 傷口分析
                var analysis = await _api.AnalyzeWoundAsync(
                    bytes, fileName, contentType, ct: ct);

                return new BatchItemResult(filePath, true, analysis, quality, null, sw.Elapsed);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "批次處理失敗：{File}", filePath);
                return new BatchItemResult(filePath, false, null, null, ex.Message, sw.Elapsed);
            }
        }

        // ── 統計摘要 ─────────────────────────────────────────────────────────

        public BatchSummary Summarise(IReadOnlyList<BatchItemResult> results)
        {
            var succeeded = results.Where(r => r.Success).ToList();
            return new BatchSummary
            {
                Total               = results.Count,
                Succeeded           = succeeded.Count,
                Failed              = results.Count - succeeded.Count,
                AverageConfidence   = succeeded.Count == 0 ? 0
                    : succeeded.Average(r => r.Analysis?.Confidence ?? 0),
                AverageWoundAreaCm2 = succeeded.Count == 0 ? 0
                    : succeeded.Average(r => r.Analysis?.WoundAreaCm2 ?? 0),
                TotalProcessingTime = TimeSpan.FromTicks(results.Sum(r => r.ProcessingTime.Ticks)),
            };
        }

        // ── 工具 ─────────────────────────────────────────────────────────────

        private static bool IsImageFile(string path)
        {
            var ext = Path.GetExtension(path).ToLowerInvariant();
            return ext is ".jpg" or ".jpeg" or ".png" or ".bmp" or ".tiff";
        }

        private static string GetContentType(string path) =>
            Path.GetExtension(path).ToLowerInvariant() switch
            {
                ".png"  => "image/png",
                ".bmp"  => "image/bmp",
                ".tiff" => "image/tiff",
                _       => "image/jpeg",
            };
    }

    public class BatchSummary
    {
        public int     Total               { get; set; }
        public int     Succeeded           { get; set; }
        public int     Failed              { get; set; }
        public double  AverageConfidence   { get; set; }
        public double  AverageWoundAreaCm2 { get; set; }
        public TimeSpan TotalProcessingTime { get; set; }
    }
}
