using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace WoundMeasurement.Core.Services
{
    // ── 標註資料模型 ───────────────────────────────────────────────────────────

    public class WoundAnnotation
    {
        public string   AnnotationId   { get; set; } = Guid.NewGuid().ToString();
        public string   ImagePath      { get; set; } = "";
        public string   PatientId      { get; set; } = "";
        public string   DoctorId       { get; set; } = "";
        public DateTime CreatedAt      { get; set; } = DateTime.UtcNow;
        public DateTime? UpdatedAt     { get; set; }

        // 醫師手動修正值
        public string?  CorrectedWoundType    { get; set; }
        public int?     CorrectedSeverity     { get; set; }   // 1–4
        public double?  CorrectedAreaCm2      { get; set; }
        public string?  ClinicalNote          { get; set; }
        public string   ApprovalStatus        { get; set; } = "pending";  // pending / approved / rejected

        // 標註品質（供增量訓練篩選）
        public string   AnnotationQuality     { get; set; } = "pending";  // pending / approved / rejected
        public bool     UsedForTraining       { get; set; } = false;
    }

    public class AnnotationFilter
    {
        public string? PatientId     { get; set; }
        public string? DoctorId      { get; set; }
        public string? ApprovalStatus{ get; set; }
        public DateTime? FromDate    { get; set; }
        public DateTime? ToDate      { get; set; }
    }

    // ── Service ───────────────────────────────────────────────────────────────

    /// <summary>
    /// 醫師標註服務：管理傷口影像的人工標注、審核與訓練資料篩選。
    /// 對應 iOS / Android DoctorAnnotation 功能模組。
    /// 本實作以 JSON 檔案持久化（正式環境應替換為 SQLite / 雲端 DB）。
    /// </summary>
    public class DoctorAnnotationService
    {
        private readonly ILogger<DoctorAnnotationService> _logger;
        private readonly string                           _storePath;
        private readonly List<WoundAnnotation>            _annotations = new();
        private readonly JsonSerializerOptions            _json = new(JsonSerializerDefaults.Web)
        {
            WriteIndented = true
        };

        public DoctorAnnotationService(
            ILogger<DoctorAnnotationService> logger,
            string storePath = "annotations.json")
        {
            _logger    = logger;
            _storePath = storePath;
            LoadFromDisk();
        }

        // ── CRUD ─────────────────────────────────────────────────────────────

        public WoundAnnotation Create(WoundAnnotation annotation)
        {
            _annotations.Add(annotation);
            SaveToDisk();
            _logger.LogInformation("標注已建立 id={Id}", annotation.AnnotationId);
            return annotation;
        }

        public WoundAnnotation? GetById(string annotationId) =>
            _annotations.FirstOrDefault(a => a.AnnotationId == annotationId);

        public IReadOnlyList<WoundAnnotation> Query(AnnotationFilter? filter = null)
        {
            IEnumerable<WoundAnnotation> q = _annotations;
            if (filter == null) return q.ToList();

            if (filter.PatientId      != null) q = q.Where(a => a.PatientId      == filter.PatientId);
            if (filter.DoctorId       != null) q = q.Where(a => a.DoctorId       == filter.DoctorId);
            if (filter.ApprovalStatus != null) q = q.Where(a => a.ApprovalStatus == filter.ApprovalStatus);
            if (filter.FromDate       != null) q = q.Where(a => a.CreatedAt      >= filter.FromDate);
            if (filter.ToDate         != null) q = q.Where(a => a.CreatedAt      <= filter.ToDate);

            return q.OrderByDescending(a => a.CreatedAt).ToList();
        }

        public bool Update(WoundAnnotation updated)
        {
            var idx = _annotations.FindIndex(a => a.AnnotationId == updated.AnnotationId);
            if (idx < 0) return false;
            updated.UpdatedAt = DateTime.UtcNow;
            _annotations[idx] = updated;
            SaveToDisk();
            return true;
        }

        public bool Delete(string annotationId)
        {
            var removed = _annotations.RemoveAll(a => a.AnnotationId == annotationId) > 0;
            if (removed) SaveToDisk();
            return removed;
        }

        // ── 審核流程 ──────────────────────────────────────────────────────────

        /// <summary>批准標注（並標記可用於訓練）</summary>
        public bool Approve(string annotationId, string? note = null)
        {
            var a = GetById(annotationId);
            if (a == null) return false;
            a.ApprovalStatus   = "approved";
            a.AnnotationQuality = "approved";
            if (note != null) a.ClinicalNote = note;
            a.UpdatedAt = DateTime.UtcNow;
            SaveToDisk();
            _logger.LogInformation("標注已批准 id={Id}", annotationId);
            return true;
        }

        /// <summary>拒絕標注</summary>
        public bool Reject(string annotationId, string reason)
        {
            var a = GetById(annotationId);
            if (a == null) return false;
            a.ApprovalStatus   = "rejected";
            a.AnnotationQuality = "rejected";
            a.ClinicalNote     = reason;
            a.UpdatedAt        = DateTime.UtcNow;
            SaveToDisk();
            return true;
        }

        /// <summary>取得已批准且尚未用於訓練的標注（供雲端 API 送出）</summary>
        public IReadOnlyList<WoundAnnotation> GetApprovedForTraining() =>
            _annotations
                .Where(a => a.ApprovalStatus == "approved" && !a.UsedForTraining)
                .ToList();

        /// <summary>標記一批標注已送出訓練</summary>
        public void MarkAsUsedForTraining(IEnumerable<string> annotationIds)
        {
            var ids = new HashSet<string>(annotationIds);
            foreach (var a in _annotations.Where(x => ids.Contains(x.AnnotationId)))
                a.UsedForTraining = true;
            SaveToDisk();
        }

        // ── 統計 ──────────────────────────────────────────────────────────────

        public AnnotationStats GetStats() => new()
        {
            Total    = _annotations.Count,
            Pending  = _annotations.Count(a => a.ApprovalStatus == "pending"),
            Approved = _annotations.Count(a => a.ApprovalStatus == "approved"),
            Rejected = _annotations.Count(a => a.ApprovalStatus == "rejected"),
            ReadyForTraining = _annotations.Count(a => a.ApprovalStatus == "approved" && !a.UsedForTraining),
        };

        // ── 持久化 ────────────────────────────────────────────────────────────

        private void SaveToDisk()
        {
            try
            {
                var json = JsonSerializer.Serialize(_annotations, _json);
                File.WriteAllText(_storePath, json);
            }
            catch (Exception ex) { _logger.LogError(ex, "標注儲存失敗"); }
        }

        private void LoadFromDisk()
        {
            if (!File.Exists(_storePath)) return;
            try
            {
                var json = File.ReadAllText(_storePath);
                var loaded = JsonSerializer.Deserialize<List<WoundAnnotation>>(json, _json);
                if (loaded != null) _annotations.AddRange(loaded);
                _logger.LogInformation("載入 {Count} 筆標注", _annotations.Count);
            }
            catch (Exception ex) { _logger.LogWarning(ex, "標注載入失敗，以空資料集啟動"); }
        }
    }

    public class AnnotationStats
    {
        public int Total            { get; set; }
        public int Pending          { get; set; }
        public int Approved         { get; set; }
        public int Rejected         { get; set; }
        public int ReadyForTraining { get; set; }
    }
}
