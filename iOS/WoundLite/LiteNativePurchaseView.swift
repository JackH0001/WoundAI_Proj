import SwiftUI

/// Sandbox composition view only. Requires an explicitly injected trusted bridge;
/// the production tab hierarchy does not instantiate this view.
struct LiteNativePurchaseView: View {
    @ObservedObject var store: LiteNativePurchaseStore
    var body: some View {
        Form {
            Section("服務訂閱驗收") {
                Text("購買會綁定目前已驗證的服務帳號。價格與付款條件以 Apple 購買畫面為準。")
                Button("查看並訂閱") { Task { await store.purchase() } }
                Button("恢復購買") { Task { await store.restore() } }
                Button("核對未完成購買") { Task { await store.reconcile() } }
            }.disabled(store.busy)
            Section("核對狀態") {
                if store.busy { ProgressView("正在核對，請勿重複購買…") }
                Text(store.message).accessibilityIdentifier("nativePurchaseStatus")
                Text("訂閱不會重新計算今天已使用的次數；研究同意與資料撤回另行處理。")
            }
        }
        .navigationTitle("訂閱與恢復購買")
        .task { store.startObserving(); await store.reconcile() }
    }
}
