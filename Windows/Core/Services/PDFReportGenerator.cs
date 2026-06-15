using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using WoundMeasurement.Core.Models;

namespace WoundMeasurement.Core.Services
{
    // ── 報告設定 ──────────────────────────────────────────────────────────────

    public class ReportConfiguration
    {
        public bool   IncludeImages           { get; set; } = true;
        public bool   IncludeMeasurementHistory{ get; set; } = true;
        public bool   IncludeRecommendations  { get; set; } = true;
        public string ReportLanguage          { get; set; } = "繁體中文";
        public string HospitalName            { get; set; } = "";
        public string DepartmentName          { get; set; } = "";
    }

    // ── 報告資料 ──────────────────────────────────────────────────────────────

    public class WoundReportData
    {
        public string         PatientId         { get; set; } = "";
        public string         PatientName       { get; set; } = "";
        public DateTime       MeasurementDate   { get; set; } = DateTime.Now;
        public AnalysisResponse? Analysis       { get; set; }
        public QualityResponse?  Quality        { get; set; }
        public string?        ImagePath         { get; set; }
        public string?        CliniciansNote    { get; set; }
        public List<WoundReportData> History    { get; set; } = new();
    }

    // ── Service ───────────────────────────────────────────────────────────────

    /// <summary>
    /// 傷口量測 PDF 報告產生器（Windows 平台）。
    /// 使用純文字 HTML → 轉換為 PDF 流程（需 wkhtmltopdf 或 PuppeteerSharp）。
    /// 本實作輸出結構化 HTML，可進一步呼叫外部程式轉換為 PDF。
    /// 對應 iOS PDFReportGenerator。
    /// </summary>
    public class PDFReportGenerator
    {
        private readonly ILogger<PDFReportGenerator> _logger;

        public PDFReportGenerator(ILogger<PDFReportGenerator> logger) =>
            _logger = logger;

        // ── 主要方法 ──────────────────────────────────────────────────────────

        /// <summary>
        /// 產生單一量測報告，輸出 HTML 檔案至指定路徑。
        /// </summary>
        public async Task<string> GenerateReportAsync(
            WoundReportData    data,
            string             outputDir,
            ReportConfiguration? config = null)
        {
            config ??= new ReportConfiguration();
            var fileName = $"WoundReport_{data.PatientId}_{data.MeasurementDate:yyyyMMdd_HHmmss}.html";
            var filePath = Path.Combine(outputDir, fileName);

            Directory.CreateDirectory(outputDir);

            var html = BuildHtml(data, config);
            await File.WriteAllTextAsync(filePath, html, Encoding.UTF8);

            _logger.LogInformation("報告已產生：{Path}", filePath);
            return filePath;
        }

        /// <summary>
        /// 批次產生報告（一筆 BatchItemResult 對應一份報告）。
        /// </summary>
        public async Task<List<string>> GenerateBatchReportsAsync(
            IReadOnlyList<BatchItemResult> batchResults,
            string                         patientId,
            string                         outputDir,
            ReportConfiguration?           config = null)
        {
            var paths = new List<string>();
            foreach (var item in batchResults.Where(r => r.Success))
            {
                var data = new WoundReportData
                {
                    PatientId       = patientId,
                    MeasurementDate = DateTime.Now,
                    Analysis        = item.Analysis,
                    Quality         = item.Quality,
                    ImagePath       = item.FilePath,
                };
                paths.Add(await GenerateReportAsync(data, outputDir, config));
            }
            return paths;
        }

        // ── HTML 建構 ─────────────────────────────────────────────────────────

        private static string BuildHtml(WoundReportData data, ReportConfiguration cfg)
        {
            var sb = new StringBuilder();
            sb.AppendLine("<!DOCTYPE html><html lang=\"zh-Hant\"><head>");
            sb.AppendLine("<meta charset=\"UTF-8\">");
            sb.AppendLine("<title>傷口量測報告</title>");
            sb.AppendLine(HtmlStyles());
            sb.AppendLine("</head><body>");

            // 標題
            sb.AppendLine("<div class=\"header\">");
            if (!string.IsNullOrEmpty(cfg.HospitalName))
                sb.AppendLine($"<div class=\"hospital\">{cfg.HospitalName}</div>");
            sb.AppendLine("<h1>傷口量測分析報告</h1>");
            sb.AppendLine($"<p>報告日期：{data.MeasurementDate:yyyy 年 MM 月 dd 日 HH:mm}</p>");
            sb.AppendLine("</div>");

            // 患者資料
            sb.AppendLine("<section class=\"section\">");
            sb.AppendLine("<h2>患者資料</h2>");
            sb.AppendLine("<table class=\"info-table\">");
            sb.AppendLine($"<tr><th>患者編號</th><td>{data.PatientId}</td></tr>");
            if (!string.IsNullOrEmpty(data.PatientName))
                sb.AppendLine($"<tr><th>患者姓名</th><td>{data.PatientName}</td></tr>");
            sb.AppendLine("</table></section>");

            // 量測結果
            if (data.Analysis != null)
            {
                var a = data.Analysis;
                sb.AppendLine("<section class=\"section\">");
                sb.AppendLine("<h2>量測結果</h2>");
                sb.AppendLine("<table class=\"info-table\">");
                sb.AppendLine($"<tr><th>傷口類型</th><td>{a.WoundType ?? "未知"}</td></tr>");
                sb.AppendLine($"<tr><th>嚴重度</th><td>{(a.SeverityScore.HasValue ? $"{a.SeverityScore} / 4 級" : "—")}</td></tr>");
                sb.AppendLine($"<tr><th>傷口面積</th><td>{(a.WoundAreaCm2.HasValue ? $"{a.WoundAreaCm2:F2} cm²" : "—")}</td></tr>");
                sb.AppendLine($"<tr><th>傷口周長</th><td>{(a.WoundPerimeterCm.HasValue ? $"{a.WoundPerimeterCm:F2} cm" : "—")}</td></tr>");
                sb.AppendLine($"<tr><th>AI 置信度</th><td>{a.Confidence:P0}</td></tr>");
                sb.AppendLine($"<tr><th>模型版本</th><td>{a.ModelVersion}</td></tr>");
                sb.AppendLine("</table>");

                // 組織成分
                if (a.TissueComposition != null)
                {
                    var t = a.TissueComposition;
                    sb.AppendLine("<h3>組織成分</h3>");
                    sb.AppendLine("<div class=\"tissue-bar\">");
                    AppendTissueBar(sb, "肉芽組織", t.Granulation, "#4caf50");
                    AppendTissueBar(sb, "腐肉",     t.Slough,      "#ff9800");
                    AppendTissueBar(sb, "壞死組織", t.Necrotic,    "#f44336");
                    sb.AppendLine("</div>");
                }
                sb.AppendLine("</section>");
            }

            // 影像品質
            if (cfg.IncludeImages && data.Quality != null)
            {
                var q = data.Quality;
                sb.AppendLine("<section class=\"section\">");
                sb.AppendLine("<h2>影像品質</h2>");
                sb.AppendLine("<table class=\"info-table\">");
                sb.AppendLine($"<tr><th>整體分數</th><td>{q.OverallScore:F1} / 100</td></tr>");
                sb.AppendLine($"<tr><th>清晰度</th><td>{q.BlurScore:F1}</td></tr>");
                sb.AppendLine($"<tr><th>信噪比</th><td>{q.SnrScore:F1}</td></tr>");
                sb.AppendLine($"<tr><th>建議</th><td>{q.Recommendation}</td></tr>");
                sb.AppendLine("</table></section>");
            }

            // 臨床備注
            if (!string.IsNullOrEmpty(data.CliniciansNote))
            {
                sb.AppendLine("<section class=\"section\">");
                sb.AppendLine("<h2>臨床備注</h2>");
                sb.AppendLine($"<p>{System.Web.HttpUtility.HtmlEncode(data.CliniciansNote)}</p>");
                sb.AppendLine("</section>");
            }

            // 頁尾
            sb.AppendLine("<div class=\"footer\">");
            sb.AppendLine("<p>本報告由 WoundAI 智慧傷口量測系統自動產生，僅供臨床參考，不構成最終診斷依據。</p>");
            sb.AppendLine($"<p>產生時間：{DateTime.Now:yyyy-MM-dd HH:mm:ss}</p>");
            sb.AppendLine("</div>");

            sb.AppendLine("</body></html>");
            return sb.ToString();
        }

        private static void AppendTissueBar(StringBuilder sb, string label, double ratio, string color)
        {
            sb.AppendLine($"<div class=\"tissue-row\">");
            sb.AppendLine($"  <span class=\"tissue-label\">{label}</span>");
            sb.AppendLine($"  <div class=\"bar-bg\"><div class=\"bar-fill\" style=\"width:{ratio:P0};background:{color}\"></div></div>");
            sb.AppendLine($"  <span class=\"tissue-pct\">{ratio:P1}</span>");
            sb.AppendLine($"</div>");
        }

        private static string HtmlStyles() => @"
<style>
  body { font-family: 'Microsoft JhengHei', Arial, sans-serif; margin: 40px; color: #333; }
  .header { text-align: center; border-bottom: 2px solid #1976d2; padding-bottom: 16px; margin-bottom: 24px; }
  .header h1 { color: #1976d2; margin: 8px 0; }
  .hospital { font-size: 1.1em; font-weight: bold; }
  .section { margin-bottom: 24px; }
  h2 { color: #1976d2; border-left: 4px solid #1976d2; padding-left: 8px; }
  h3 { color: #555; margin-top: 16px; }
  .info-table { border-collapse: collapse; width: 100%; }
  .info-table th, .info-table td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
  .info-table th { background: #e3f2fd; width: 30%; }
  .tissue-row { display: flex; align-items: center; margin: 6px 0; }
  .tissue-label { width: 80px; font-size: 0.9em; }
  .bar-bg { flex: 1; background: #eee; border-radius: 4px; height: 18px; margin: 0 8px; }
  .bar-fill { height: 100%; border-radius: 4px; }
  .tissue-pct { width: 50px; text-align: right; font-size: 0.9em; }
  .footer { margin-top: 40px; border-top: 1px solid #ddd; padding-top: 12px; font-size: 0.8em; color: #888; }
</style>";
    }
}
