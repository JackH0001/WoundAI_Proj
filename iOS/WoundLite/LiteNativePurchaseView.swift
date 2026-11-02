import SwiftUI

/// The candidate injects a trusted service bridge. Reading the disclosures needs no service access.
struct LiteNativePurchaseView: View {
    @ObservedObject var store: LiteNativePurchaseStore
    let canManagePurchases: Bool
    var body: some View {
        Form {
            Section("個人雲端辨識") {
                Text("免費方案每日可成功辨識 2 次。Plus 每日共 10 次，包含當日已使用的次數。").accessibilityIdentifier("lite.offer.quota")
                Text("免費可同時追蹤 1 處傷口，Plus 每帳戶最多 5 處。每日辨識次數由各處傷口合計。結束追蹤可換另一處，舊時間軸仍可查看及匯出 PDF。")
                Text("額度每日在 UTC 0 時重算，台灣時間為上午 8 時。處理中的辨識會暫時保留名額。查看已保存的結果不扣次數。")
                    .font(.footnote)
            }
            Section("每月自動續訂") {
                if store.loadingOffer {
                    ProgressView("正在讀取 Apple 的商品與價格…")
                } else if let offer = store.offer {
                    Text(offer.name).font(.headline)
                    Text(verbatim: "\(offer.displayPrice)／月")
                        .font(.title2.weight(.semibold))
                        .accessibilityIdentifier("lite.offer.price")
                    Text("每月自動續訂，可在 Apple 訂閱設定取消。購買會綁定目前的服務帳號。")
                        .font(.footnote)
                    if canManagePurchases {
                        Button("以 \(offer.displayPrice)／月訂閱") { Task { await store.purchase(accepting: offer) } }
                            .accessibilityIdentifier("lite.offer.buy")
                    }
                } else {
                    Text("尚未取得可購買的每月方案與價格。")
                    Button("重新讀取 Apple 商品") { Task { await store.loadOffer() } }
                }
            }.disabled(store.busy)
            Section("訂閱前可先閱讀") {
                LitePrivacyLink()
                LiteTermsLink()
            }
            if canManagePurchases {
                Section("既有訂閱") {
                    Button("恢復購買") { Task { await store.restore() } }
                    Button("核對未完成購買") { Task { await store.reconcile() } }
                    Link("管理 Apple 訂閱", destination: URL(string: "https://apps.apple.com/account/subscriptions")!)
                }.disabled(store.busy)
                Section("核對狀態") {
                    if store.busy { ProgressView("正在核對，請勿重複購買…") }
                    Text(store.message).accessibilityIdentifier("nativePurchaseStatus")
                    Text("研究上傳由您另行決定。").font(.footnote)
                }
            } else {
                Text("這裡只向 Apple 查詢當地月費。要訂閱或恢復購買，請先核對裝置並登入服務帳號。研究上傳由您另行決定。")
                    .font(.footnote).accessibilityIdentifier("lite.offer.preview")
            }
        }
        .navigationTitle(canManagePurchases ? "訂閱與恢復購買" : "Plus 方案與價格")
        .task(id: canManagePurchases) {
            await store.openOffers(canManagePurchases: canManagePurchases)
        }
    }
}
