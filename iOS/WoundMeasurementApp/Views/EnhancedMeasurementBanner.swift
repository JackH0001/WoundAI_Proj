import SwiftUI

struct MeasurementBanner: View {
    let pixelScaleSource: String
    let warning: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: warning == nil ? "checkmark.shield" : "exclamationmark.triangle.fill")
                    .foregroundColor(warning == nil ? .green : .orange)
                Text("像素比例來源：\(pixelScaleSource)")
                    .font(.subheadline)
            }
            if let warning = warning {
                Text(warning)
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
        .padding(10)
        .background((warning == nil ? Color.green.opacity(0.1) : Color.orange.opacity(0.12)))
        .cornerRadius(10)
    }
}


