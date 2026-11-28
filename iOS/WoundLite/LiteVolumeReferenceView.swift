import SwiftUI

/// Keep diagnostic values, but never present them as validated wound volume.
struct LiteVolumeReferenceView: View {
    @Environment(\.locale) private var locale
    private var copy: LiteHistoryCopy { LiteHistoryCopy(locale: locale) }
    let volume: Double?
    let depth: Double?

    var body: some View {
        Text(copy.text("目前深度與體積尚未通過實物精度驗證，不作傷口量測或癒合判定。"))
            .font(.caption).foregroundStyle(.secondary)
        if let volume, let depth, volume.isFinite, depth.isFinite, volume >= 0, depth >= 0 {
            DisclosureGroup(copy.text("研究參考值與限制")) {
                Text(copy.format("可見凹陷的計算值 %@ mL・最大深度 %@ mm", copy.decimal(volume), String(format: "%.1f", depth)))
                Text(copy.text("只代表深度圖相對自動擬合參考面的計算；可能嚴重低估。接近零不代表沒有凹陷，不包含看不到的隧道或完整空腔，也未扣抵凸起組織。原始深度保留供研究核對。"))
            }.font(.caption)
        } else {
            Text(copy.text("本次沒有可提供的深度／體積參考值，不以零代替。"))
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
