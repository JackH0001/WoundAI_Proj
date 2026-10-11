import Foundation

/// Annotation acceptance and original depth persistence are distinct acknowledgements.
/// Callers persist the capture before starting; a depth failure never clears annotation success.
@MainActor
enum ClinicalUploadFlow {
    static func run(
        annotate: () async throws -> AnnotationOutcome,
        acknowledge: () async throws -> Void,
        depth: (() async throws -> Void)?,
        progress: (String) -> Void
    ) async -> String {
        progress("1/2 傳送標註並等待後端保存確認…")
        let outcome: AnnotationOutcome
        do { outcome = try await annotate() }
        catch { return "⚠️ 標註尚未確認保存：\(error.localizedDescription)\n本機紀錄已保留，可重新送出；不需重新修邊。" }
        if case .rejected(let reason) = outcome { return "⚠️ 標註未保存：\(reason)" }
        // Both stored and duplicate acknowledgements permit the same image's depth upload.
        do { try await acknowledge() }
        catch { return "⚠️ 雲端標註已存在，但本機送件狀態保存失敗。請重試同步，不需重新修邊。" }
        guard let depth else {
            return "✅ 標註已確認保存。本筆沒有待上傳的本機深度；此訊息不代表完整 RGB-D 已驗收。"
        }
        progress("2/2 傳送原始深度並等待後端保存確認…")
        do { try await depth() }
        catch { return "⚠️ 標註已保存；原始深度尚未確認完成：\(error.localizedDescription)\n本機資料已保留，待傳佇列會接續重試深度，不需重新修邊或重送標註。" }
        return "✅ 標註與原始深度皆已由後端確認保存。"
    }
}
