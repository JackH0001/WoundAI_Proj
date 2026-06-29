import SwiftUI

/**
 量測結果畫面(SwiftUI 骨架)：觀察 [MeasureViewModel] → 顯示面積/組織/PUSH/信心度，
 並導向「醫師確認・修邊」或「存入時間軸」。輔助、非診斷、需醫師確認。
 */
struct MeasureView: View {
    @ObservedObject var vm: MeasureViewModel
    var onReview: () -> Void
    var onSaveToTimeline: () -> Void

    private func pct(_ v: Double?) -> String { v == nil ? "0%" : "\(Int(v! * 100))%" }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("量測結果").font(.title2).bold()
            if vm.loading {
                ProgressView().frame(maxWidth: .infinity)
            } else if let e = vm.error {
                Text("分析失敗：\(e)").foregroundColor(.red)
            } else if let r = vm.result {
                VStack(alignment: .leading, spacing: 6) {
                    Text("面積：" + (r.areaCm2.map { String(format: "%.2f cm²", $0) } ?? "未校正(無貼紙)"))
                        .font(.headline)
                    Text("PUSH：" + (r.push.partial.map(String.init) ?? "-") +
                         (r.push.full.map { "（含滲液 \($0)）" } ?? "（滲液待醫師輸入）"))
                    Text("組織：肉芽 \(pct(r.tissueFrac["granulation"])) · 腐肉 \(pct(r.tissueFrac["slough"])) · 壞死 \(pct(r.tissueFrac["necrosis"]))")
                    Text("路由：\(r.route) · 信心 \(Int(r.confidence * 100))%")
                }
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))

                if r.confidence < 0.70 {
                    Text("信心度偏低，建議醫師確認").font(.footnote).foregroundColor(.orange)
                }
                Button(action: onReview) { Text("醫師確認・修邊").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent)
                Button(action: onSaveToTimeline) { Text("存入個案時間軸").frame(maxWidth: .infinity) }
                    .buttonStyle(.bordered)
                Text(r.disclaimer).font(.caption2).foregroundColor(.secondary)
            } else {
                Text("尚無結果，請先拍攝。").foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(16)
    }
}
