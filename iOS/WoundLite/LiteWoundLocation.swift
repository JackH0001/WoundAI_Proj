import SwiftUI

/// Stable codes are stored; translated display labels are not identifiers.
struct LiteWoundLocation: Codable, Equatable {
    var side: String
    var site: String

    static let sides = [("left", "左"), ("midline", "中線／不分側"), ("right", "右")]
    static let sites: [(String, String)] = [
        ("scalp", "頭皮"), ("face", "臉部"), ("neck", "頸部"),
        ("chest", "胸部"), ("abdomen", "腹部"), ("upper_back", "上背"),
        ("lower_back", "下背"), ("sacrococcygeal", "薦尾部"), ("perineum", "會陰部"),
        ("buttock", "臀部"), ("hip", "髖部"), ("upper_arm", "上臂"),
        ("elbow", "肘部"), ("forearm", "前臂"), ("wrist", "手腕"),
        ("hand_dorsum", "手背"), ("palm", "手掌"), ("finger", "手指"),
        ("thigh", "大腿"), ("knee_front", "膝前"), ("knee_back", "膝後"),
        ("lower_leg_front", "小腿前側"), ("lower_leg_back", "小腿後側"),
        ("lower_leg_medial", "小腿內側"), ("lower_leg_lateral", "小腿外側"),
        ("ankle_medial", "內踝"), ("ankle_lateral", "外踝"),
        ("heel", "足跟"), ("foot_dorsum", "足背"), ("sole", "足底"), ("toe", "足趾"),
        ("other", "其他")
    ]
    private static let centralSites: Set<String> = ["scalp", "face", "neck", "chest", "abdomen", "upper_back", "lower_back", "sacrococcygeal", "perineum"]
    var isComplete: Bool {
        Self.sides.contains { $0.0 == side } && Self.sites.contains { $0.0 == site }
        && (side != "midline" || (Self.centralSites.contains(site) || site == "other"))
    }
    var label: String {
        let sideLabel = Self.sides.first { $0.0 == side }?.1 ?? "未選側別"
        let siteLabel = Self.sites.first { $0.0 == site }?.1 ?? "未選部位"
        return "\(sideLabel)・\(siteLabel)"
    }
}

struct LiteLocationFields: View {
    @Binding var side: String
    @Binding var site: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { sideButtons; siteMenu }
                VStack(alignment: .leading, spacing: 8) { sideButtons; siteMenu }
            }
            if !side.isEmpty, !site.isEmpty, !LiteWoundLocation(side: side, site: site).isComplete {
                Text("這個部位請選左側或右側。")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }
    private var sideButtons: some View {
        HStack(spacing: 4) {
            ForEach(LiteWoundLocation.sides, id: \.0) { code, title in
                Button { side = code } label: {
                    Text(title).font(.caption).fixedSize()
                        .padding(.horizontal, 8).frame(minHeight: 44)
                }
                .buttonStyle(.plain)
                .foregroundStyle(side == code ? Color.blue : Color.primary)
                .background(RoundedRectangle(cornerRadius: 8).fill(side == code ? Color.blue.opacity(0.18) : Color.secondary.opacity(0.10)))
                .accessibilityLabel("側別：\(title)")
                .accessibilityAddTraits(side == code ? .isSelected : [])
            }
        }
    }
    private var siteMenu: some View {
        Picker("詳細部位", selection: $site) {
            Text("選擇詳細部位").tag("")
            ForEach(LiteWoundLocation.sites, id: \.0) { code, title in Text(title).tag(code) }
        }
        .pickerStyle(.menu)
        .accessibilityLabel("傷口詳細部位")
    }
}
