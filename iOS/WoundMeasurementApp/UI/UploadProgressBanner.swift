import SwiftUI

/// Indeterminate: bytes sent are not equivalent to a durable server acknowledgement.
struct UploadProgressBanner: View {
    let message: String
    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
            VStack(alignment: .leading, spacing: 4) {
                Text(message).font(.subheadline).bold()
                Text("請保持 App 在前景。網路或主機啟動可能需要等待；尚未顯示完成前，資料仍在處理。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial)
        .accessibilityElement(children: .combine)
    }
}
