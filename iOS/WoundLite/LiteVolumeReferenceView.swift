import SwiftUI

/// Keep diagnostic values, but never present them as validated wound volume.
struct LiteVolumeReferenceView: View {
    let volume: Double?
    let depth: Double?

    var body: some View {
        Text("目前深度與體積尚未通過實物精度驗證，不作傷口量測或癒合判定。")
            .font(.caption).foregroundStyle(.secondary)
        if let volume, let depth, volume.isFinite, depth.isFinite, volume >= 0, depth >= 0 {
            DisclosureGroup("研究參考值與限制") {
                Text(String(format: "可見凹陷的計算值 %.2f mL・最大深度 %.1f mm", volume, depth))
                Text("只代表深度圖相對自動擬合參考面的計算；可能嚴重低估。接近零不代表沒有凹陷，不包含看不到的隧道或完整空腔，也未扣抵凸起組織。原始深度保留供研究核對。")
            }.font(.caption)
        } else {
            Text("本次沒有可提供的深度／體積參考值，不以零代替。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
