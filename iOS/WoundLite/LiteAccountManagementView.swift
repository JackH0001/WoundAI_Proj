import SwiftUI

/// Requires the same protected journal in identity/deletion composition.
/// Not constructed by default startup until disposition and live gates pass.
struct LiteAccountManagementView:View {
    @ObservedObject var identity:LiteAccountIdentityStore
    @StateObject var deletion:LiteAccountDeletionStore
    private let researchLink: LiteResearchDeletionLink?
    @State private var confirming=false
    init(identity:LiteAccountIdentityStore,service:any LiteAccountDeletionService, researchLink: LiteResearchDeletionLink? = nil)throws {
        guard identity.service.origin==service.origin else{throw LiteDeletionFailure.conflict}
        self.identity=identity
        self.researchLink=researchLink
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
                    Text("刪除服務帳號時，也會撤回與這個帳號連結的研究參與。我們會停止雲端服務，撤銷 Apple 登入授權，並核對可對應的研究影像、深度及標註。")
                    Text("目前驗收版會分別回報各項處理進度，完整帳號資料清理仍在驗證中；提出要求不代表資料已刪除。")
                        .foregroundStyle(.secondary)
                    Button("刪除帳號…",role:.destructive){confirming=true}
                        .disabled(identity.busy || deletion.busy)
                        .accessibilityIdentifier("accountDeletionConfirm")
                }
            }
            if let original = deletion.record, original.researchWithdrawalConfirmed != true && original.receipt?.research_withdrawal != "requested" {
                Button("確認原刪帳要求也撤回研究…", role:.destructive){confirming=true}
                    .disabled(deletion.busy)
            }
            Section("刪帳進度") {
                if deletion.busy{ProgressView("正在核對原要求…")}
                Text(deletion.message).accessibilityIdentifier("accountDeletionStatus")
                if let r=deletion.record?.receipt {
                    LabeledContent("研究撤回",value:r.research_withdrawal == "requested" ? "已受理，清理待核對" : "尚未確認受理")
                    LabeledContent("Apple 授權",value:r.apple_revocation=="confirmed" ? "撤銷已確認":"處理／核對中")
                    if let progress=r.disposition {
                        LabeledContent("目前服務結果",value:progress.serviceText)
                        LabeledContent("Apple 憑證金鑰",value:progress.keyText)
                        Text("封存副本、舊服務可對應的資料、歷史版本及備份仍待核對。")
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
                if let pending=deletion.record,pending.receipt==nil,pending.researchWithdrawalConfirmed == true,!deletion.needsReview {
                    Button("核對身分並繼續原刪帳要求") {Task{await deletion.reauthenticate()}}
                        .disabled(deletion.busy || identity.busy).accessibilityIdentifier("accountDeletionReauthenticate")
                    Text("只有按此按鈕才會開啟 Apple 身分核對；請使用原帳號。這不會恢復雲端服務、研究上傳或訂閱額度。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let researchLink, let record = deletion.record, record.receipt?.research_withdrawal == "requested" {
                LiteResearchLinkSection(link: researchLink, record: record)
            }
            Section("保留與訂閱") {
                Text("手機照片與紀錄保留。如果只想退出研究，可在「資料與撤回」單獨撤回並保留服務帳號。")
                Text("刪除帳號不等於取消 Apple 訂閱。如有訂閱，請另至 Apple 訂閱管理確認。")
                Link("管理 Apple 訂閱",destination:URL(string:"https://apps.apple.com/account/subscriptions")!)
            }
        }.navigationTitle("帳號與刪除")
        .task{await deletion.reload()}
        .alert("確認提出刪帳要求？",isPresented:$confirming){
            Button("確認刪帳並撤回研究",role:.destructive){Task{await deletion.requestConfirmed()}}
            Button("取消",role:.cancel){}
        }message:{Text("確認後，這部手機會先暫停研究上傳，再提出刪帳與研究撤回。影像、深度、標註及封存副本依可確認的範圍清理。手機紀錄保留；如有 Apple 訂閱，請另行取消。")}
    }
}

private struct LiteResearchLinkSection: View {
    @ObservedObject var link: LiteResearchDeletionLink
    let record: LiteAccountDeletionCheckpoint
    @State private var confirming = false

    var body: some View {
        Section("核對本機研究登記") {
            Text(link.message).accessibilityIdentifier("researchLinkProgress")
            if !link.confirmed {
                Button("核對這部手機的研究登記…") { confirming = true }
                    .disabled(link.busy)
                if link.busy { ProgressView("核對中…") }
            }
        }
        .task(id: record.operation) { await link.reload(record) }
        .alert("將本機原研究登記連結至這筆刪帳要求？", isPresented: $confirming) {
            Button("確認連結並核對") { Task { await link.confirm(record) } }
            Button("返回", role: .cancel) {}
        } message: {
            Text("兩個服務會各自核對這部手機原有的登記，讓這筆撤回能找到對應的研究資料。只傳核對所需的識別資料，手機照片與深度會保留。")
        }
    }
}
