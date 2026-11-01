import SwiftUI

/// Opt-in service UI component. No startup/public capture integration is enabled.
struct LiteServiceStatusView: View {
    let record: LiteServiceRecord
    let client: LiteServiceClient
    let loadJPEG: @Sendable (String) async throws -> Data?
    let onRecord: (LiteServiceRecord) -> Void
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(record.message)
            if record.canResubmit {
                Button(busy ? "處理中…" : "重新送出") {
                    guard !busy else { return }
                    busy = true
                    message = nil
                    Task { @MainActor in
                        defer { busy = false }
                        do {
                            onRecord(try await client.requestResubmission(record.captureID))
                            onRecord(try await client.resume(record.captureID, loadJPEG: loadJPEG))
                        }
                        catch { message = "尚未確認送出；原照片與紀錄保留，請先核對狀態。" }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(busy)
                .accessibilityIdentifier("lite.service.expired.resubmit")
            }
            if let message { Text(message).foregroundStyle(.secondary) }
        }
    }
}
