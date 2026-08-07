import SwiftUI

/**
 行動端導覽(SwiftUI NavigationStack 骨架)。
 流程:個案清單→個案詳情→(新增)知情同意→拍攝→量測→修邊→去識別→時間軸。
 量測畫面接真正的 [MeasureView]/[MeasureViewModel];其餘為占位(待原生實作)。
 同意僅首次新增;既有個案「繼續拍攝」由個案詳情直接到拍攝(免重簽)。
 */
enum Route: Hashable {
    case caseDetail, consent, capture, measure, review, deid, timeline
}

struct WoundRootView: View {
    @State private var path: [Route] = []
    let measureVMProvider: () -> MeasureViewModel

    var body: some View {
        NavigationStack(path: $path) {
            placeholder("個案清單", "新增個案 →") { path.append(.caseDetail) }
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .caseDetail:
                        placeholder("個案詳情", "新增→知情同意", { path.append(.consent) },
                                    secondary: "繼續拍攝(免重簽)→") { path.append(.capture) }
                    case .consent: placeholder("知情同意+電子簽名", "同意並開始拍攝 →") { path.append(.capture) }
                    case .capture: placeholder("拍攝(品質把關)", "拍攝→量測 →") { path.append(.measure) }
                    case .measure:
                        MeasureView(vm: measureVMProvider(),
                                    onReview: { path.append(.review) },
                                    onSaveToTimeline: { path.append(.timeline) })
                    case .review: placeholder("修邊與標註", "完成→去識別 →") { path.append(.deid) }
                    case .deid: placeholder("去識別化上傳", "上傳→時間軸 →") { path.append(.timeline) }
                    case .timeline: placeholder("傷口時間軸", "＋新增量測(回拍攝) →") { path.append(.capture) }
                    }
                }
        }
    }

    @ViewBuilder
    private func placeholder(_ title: String, _ primary: String, _ onPrimary: @escaping () -> Void,
                            secondary: String? = nil, onSecondary: (() -> Void)? = nil) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.title2).bold()
            Text("（畫面骨架；UI 依 docs/mobile_technical_spec 實作）").font(.caption).foregroundColor(.secondary)
            Button(primary, action: onPrimary).buttonStyle(.borderedProminent)
            if let s = secondary, let on = onSecondary { Button(s, action: on).buttonStyle(.bordered) }
            Spacer()
        }.padding(20)
    }
}
