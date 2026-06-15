using System;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace WoundMeasurement.Core.Services
{
    // ── Request / Response DTOs ────────────────────────────────────────────

    public record LoginRequest(
        [property: JsonPropertyName("username")] string Username,
        [property: JsonPropertyName("password")] string Password);

    public record TokenResponse(
        [property: JsonPropertyName("access_token")] string AccessToken,
        [property: JsonPropertyName("token_type")]   string TokenType,
        [property: JsonPropertyName("role")]          string Role);

    public record ServiceStatusResponse(
        [property: JsonPropertyName("status")]         string Status,
        [property: JsonPropertyName("version")]        string Version,
        [property: JsonPropertyName("uptime_seconds")] double UptimeSeconds,
        [property: JsonPropertyName("models_ready")]   bool   ModelsReady,
        [property: JsonPropertyName("training_jobs")]  int    TrainingJobs);

    public record UploadResponse(
        [property: JsonPropertyName("image_id")]   string ImageId,
        [property: JsonPropertyName("filename")]   string Filename,
        [property: JsonPropertyName("size_bytes")] long   SizeBytes,
        [property: JsonPropertyName("message")]    string Message);

    public record QualityResponse(
        [property: JsonPropertyName("image_id")]        string ImageId,
        [property: JsonPropertyName("overall_score")]   double OverallScore,
        [property: JsonPropertyName("blur_score")]      double BlurScore,
        [property: JsonPropertyName("snr_score")]       double SnrScore,
        [property: JsonPropertyName("brightness_score")]double BrightnessScore,
        [property: JsonPropertyName("contrast_score")]  double ContrastScore,
        [property: JsonPropertyName("is_acceptable")]   bool   IsAcceptable,
        [property: JsonPropertyName("recommendation")]  string Recommendation);

    public record TissueComposition(
        [property: JsonPropertyName("granulation")] double Granulation,
        [property: JsonPropertyName("slough")]      double Slough,
        [property: JsonPropertyName("necrotic")]    double Necrotic);

    public record AnalysisResponse(
        [property: JsonPropertyName("image_id")]          string            ImageId,
        [property: JsonPropertyName("wound_area_cm2")]    double?           WoundAreaCm2,
        [property: JsonPropertyName("wound_perimeter_cm")]double?           WoundPerimeterCm,
        [property: JsonPropertyName("wound_volume_cm3")]  double?           WoundVolumeCm3,
        [property: JsonPropertyName("wound_type")]        string?           WoundType,
        [property: JsonPropertyName("severity_score")]    int?              SeverityScore,
        [property: JsonPropertyName("tissue_composition")]TissueComposition TissueComposition,
        [property: JsonPropertyName("confidence")]        double            Confidence,
        [property: JsonPropertyName("model_version")]     string            ModelVersion,
        [property: JsonPropertyName("calibration_method")]string?           CalibrationMethod,
        [property: JsonPropertyName("scale_mm_per_px")]   double?           ScaleMmPerPx);

    // ── Service ────────────────────────────────────────────────────────────

    /// <summary>
    /// 雲端 AI 服務 HTTP 客戶端，負責 JWT 認證與所有 API 呼叫。
    /// </summary>
    public class CloudAPIService : IDisposable
    {
        private readonly HttpClient              _http;
        private readonly ILogger<CloudAPIService> _logger;
        private readonly JsonSerializerOptions   _json = new(JsonSerializerDefaults.Web);

        private string? _accessToken;
        private DateTime _tokenExpiry = DateTime.MinValue;

        // ── 建構子 ─────────────────────────────────────────────────────────

        public CloudAPIService(string baseUrl, ILogger<CloudAPIService> logger)
        {
            _logger = logger;
            _http   = new HttpClient { BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/") };
            _http.DefaultRequestHeaders.Accept.Add(
                new MediaTypeWithQualityHeaderValue("application/json"));
        }

        // ── 認證 ───────────────────────────────────────────────────────────

        /// <summary>
        /// 登入並快取 JWT；token 有效期 24 h，提前 5 分鐘自動視為過期。
        /// </summary>
        public async Task LoginAsync(string username, string password,
                                     CancellationToken ct = default)
        {
            var req  = new LoginRequest(username, password);
            var resp = await _http.PostAsJsonAsync("api/v1/auth/login", req, _json, ct);
            resp.EnsureSuccessStatusCode();

            var token = await resp.Content.ReadFromJsonAsync<TokenResponse>(_json, ct)
                        ?? throw new InvalidOperationException("登入回應為空");

            _accessToken = token.AccessToken;
            _tokenExpiry = DateTime.UtcNow.AddHours(24).AddMinutes(-5);
            SetAuthHeader(_accessToken);
            _logger.LogInformation("CloudAPI 登入成功，角色：{Role}", token.Role);
        }

        private void SetAuthHeader(string token) =>
            _http.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Bearer", token);

        private async Task EnsureAuthenticatedAsync(
            string username, string password, CancellationToken ct)
        {
            if (_accessToken is null || DateTime.UtcNow >= _tokenExpiry)
                await LoginAsync(username, password, ct);
        }

        // ── 服務狀態 ────────────────────────────────────────────────────────

        /// <summary>GET /api/v1/status — 取得服務健康狀態。</summary>
        public async Task<ServiceStatusResponse> GetStatusAsync(CancellationToken ct = default)
        {
            var resp = await _http.GetAsync("api/v1/status", ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<ServiceStatusResponse>(_json, ct)
                   ?? throw new InvalidOperationException("狀態回應為空");
        }

        // ── 影像上傳 ────────────────────────────────────────────────────────

        /// <summary>POST /api/v1/upload — 上傳影像，回傳 image_id。</summary>
        public async Task<UploadResponse> UploadImageAsync(
            byte[] imageBytes, string fileName, string contentType = "image/jpeg",
            CancellationToken ct = default)
        {
            using var content = new MultipartFormDataContent();
            var imageContent  = new ByteArrayContent(imageBytes);
            imageContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
            content.Add(imageContent, "file", fileName);

            var resp = await _http.PostAsync("api/v1/upload", content, ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<UploadResponse>(_json, ct)
                   ?? throw new InvalidOperationException("上傳回應為空");
        }

        /// <summary>從磁碟路徑上傳影像。</summary>
        public async Task<UploadResponse> UploadImageFromFileAsync(
            string filePath, CancellationToken ct = default)
        {
            var bytes       = await File.ReadAllBytesAsync(filePath, ct);
            var fileName    = Path.GetFileName(filePath);
            var contentType = Path.GetExtension(filePath).ToLowerInvariant() switch
            {
                ".png"  => "image/png",
                ".jpg" or ".jpeg" => "image/jpeg",
                ".bmp"  => "image/bmp",
                _       => "application/octet-stream",
            };
            return await UploadImageAsync(bytes, fileName, contentType, ct);
        }

        // ── 品質評估 ────────────────────────────────────────────────────────

        /// <summary>POST /api/v1/quality — 評估影像品質（0–100 分）。</summary>
        public async Task<QualityResponse> AssessQualityAsync(
            byte[] imageBytes, string fileName, string contentType = "image/jpeg",
            CancellationToken ct = default)
        {
            using var content = new MultipartFormDataContent();
            var imageContent  = new ByteArrayContent(imageBytes);
            imageContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
            content.Add(imageContent, "image", fileName);

            var resp = await _http.PostAsync("api/v1/quality", content, ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<QualityResponse>(_json, ct)
                   ?? throw new InvalidOperationException("品質評估回應為空");
        }

        // ── 傷口分析 ────────────────────────────────────────────────────────

        /// <summary>
        /// POST /api/v1/analyze — 傷口分析（語意分割 + 分類 + 面積換算）。
        /// </summary>
        public async Task<AnalysisResponse> AnalyzeWoundAsync(
            byte[] imageBytes, string fileName,
            string  contentType      = "image/jpeg",
            string? calibrationMethod = "ruler",
            double? scaleMmPerPx     = null,
            CancellationToken ct     = default)
        {
            using var content = new MultipartFormDataContent();

            var imageContent = new ByteArrayContent(imageBytes);
            imageContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
            content.Add(imageContent, "image", fileName);

            if (calibrationMethod is not null)
                content.Add(new StringContent(calibrationMethod), "calibration_method");

            if (scaleMmPerPx.HasValue)
                content.Add(
                    new StringContent(scaleMmPerPx.Value.ToString("R")), "scale_mm_per_px");

            var resp = await _http.PostAsync("api/v1/analyze", content, ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<AnalysisResponse>(_json, ct)
                   ?? throw new InvalidOperationException("分析回應為空");
        }

        /// <summary>從磁碟路徑分析傷口影像（整合上傳 + 分析的便利方法）。</summary>
        public async Task<AnalysisResponse> AnalyzeWoundFromFileAsync(
            string  filePath,
            string? calibrationMethod = "ruler",
            double? scaleMmPerPx     = null,
            CancellationToken ct     = default)
        {
            var bytes       = await File.ReadAllBytesAsync(filePath, ct);
            var fileName    = Path.GetFileName(filePath);
            var contentType = Path.GetExtension(filePath).ToLowerInvariant() switch
            {
                ".png"  => "image/png",
                ".jpg" or ".jpeg" => "image/jpeg",
                _       => "image/jpeg",
            };
            return await AnalyzeWoundAsync(bytes, fileName, contentType,
                                           calibrationMethod, scaleMmPerPx, ct);
        }

        // ── IDisposable ────────────────────────────────────────────────────

        public void Dispose() => _http.Dispose();
    }
}
