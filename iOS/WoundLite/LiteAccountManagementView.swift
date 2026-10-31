import SwiftUI

/// Requires the same protected journal in identity/deletion composition.
/// Not constructed by default startup until disposition and live gates pass.
struct LiteAccountManagementView:View {
    @ObservedObject var identity:LiteAccountIdentityStore
    @StateObject var deletion:LiteAccountDeletionStore
    @State private var confirming=false
    init(identity:LiteAccountIdentityStore,service:any LiteAccountDeletionService)throws {
        guard identity.service.origin==service.origin else{throw LiteDeletionFailure.conflict}
        self.identity=identity
        // Construct both paths with the same durable journal; callers cannot
        // accidentally fence a different store than the service admission path.
        self._deletion=StateObject(wrappedValue:LiteAccountDeletionStore(service:service,identity:identity,journal:identity.journal,now:identity.now))
    }
    var body:some View {
        Form {
            if deletion.record==nil && !deletion.needsReview {
                Section("服務帳號") {
                    Button("使用 Apple 登入") {Task{await identity.signIn()}}
                    Button("核對未完成登入") {Task{await identity.retry()}}
                    Button("登出服務帳號") {Task{await identity.signOut()}}
                    Text(identity.message).font(.footnote)
                }.disabled(identity.busy || deletion.busy)
                Section("刪除帳號") {
                    Text("提出後會停止此帳號在各裝置上的雲端服務，並處理 Apple 登入授權。手機紀錄、Apple 訂閱及研究撤回分別處理。")
                    Text("目前驗收版會分別回報各項處理進度，完整帳號資料清理仍在驗證中；提出要求不代表資料已刪除。")
                        .foregroundStyle(.secondary)
                    Button("刪除帳號…",role:.destructive){confirming=true}
                        .disabled(identity.busy || deletion.busy)
                        .accessibilityIdentifier("accountDeletionConfirm")
                }
            }
            Section("刪帳進度") {
                if deletion.busy{ProgressView("正在核對原要求…")}
                Text(deletion.message).accessibilityIdentifier("accountDeletionStatus")
                if let r=deletion.record?.receipt {
                    LabeledContent("Apple 授權",value:r.apple_revocation=="confirmed" ? "撤銷已確認":"處理／核對中")
                    if let progress=r.disposition {
                        LabeledContent("目前服務結果",value:progress.serviceText)
                        LabeledContent("Apple 憑證金鑰",value:progress.keyText)
                        Text("歷史版本、備份、研究資料及其他帳號資料仍待處理或核對，個別步驟完成不代表全部刪除。")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        LabeledContent("帳號資料清理",value:"尚未開始")
                    }
                    LabeledContent("帳號刪除",value:"尚未完成")
                }
                if deletion.record != nil || deletion.needsReview {
                    Button("核對原刪帳進度") {Task{await deletion.retry()}}
                        .disabled(deletion.busy).accessibilityIdentifier("accountDeletionRetry")
                }
                if let pending=deletion.record,pending.receipt==nil,!deletion.needsReview {
                    Button("核對身分並繼續原刪帳要求") {Task{await deletion.reauthenticate()}}
                        .disabled(deletion.busy || identity.busy).accessibilityIdentifier("accountDeletionReauthenticate")
                    Text("只有按此按鈕才會開啟 Apple 身分核對；請使用原帳號。這不會恢復雲端服務、研究上傳或訂閱額度。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("保留與訂閱") {
                Text("手機照片與紀錄保留。研究資料的撤回請使用「資料與撤回」，不會因重新登入而自動恢復研究。")
                Text("刪除帳號不等於取消 Apple 訂閱。如有訂閱，請另至 Apple 訂閱管理確認。")
                Link("管理 Apple 訂閱",destination:URL(string:"https://apps.apple.com/account/subscriptions")!)
            }
        }.navigationTitle("帳號與刪除")
        .task{await deletion.reload()}
        .alert("確認提出刪帳要求？",isPresented:$confirming){
            Button("確認提出並停止帳號服務",role:.destructive){Task{await deletion.requestConfirmed()}}
            Button("取消",role:.cancel){}
        }message:{Text("會先保存同一筆要求再傳送；無法確認時可重試核對。手機紀錄保留，Apple 訂閱需另行取消。各項處理會分別回報，目前驗收版尚未完成全部資料清理。")}
    }
}
