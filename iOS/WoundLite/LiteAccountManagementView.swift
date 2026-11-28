import SwiftUI
import UniformTypeIdentifiers
import CoreTransferable
import AuthenticationServices

/// The service must obtain its challenge asynchronously before presenting Apple authorization.
struct LiteAppleSignInButton: View {
    let identifier: String
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Control(identifier: identifier, style: colorScheme == .dark ? .white : .black, action: action)
            .id(colorScheme)
            .frame(maxWidth: .infinity)
            .frame(height: 48)
    }

    private struct Control: UIViewRepresentable {
        let identifier: String
        let style: ASAuthorizationAppleIDButton.Style
        let action: () -> Void

        final class Coordinator: NSObject {
            var action: () -> Void
            init(action: @escaping () -> Void) { self.action = action }
            @objc func pressed() { action() }
        }

        func makeCoordinator() -> Coordinator { Coordinator(action: action) }

        func makeUIView(context: Context) -> ASAuthorizationAppleIDButton {
            let button = ASAuthorizationAppleIDButton(type: .signIn, style: style)
            button.addTarget(context.coordinator, action: #selector(Coordinator.pressed), for: .touchUpInside)
            return button
        }

        func updateUIView(_ button: ASAuthorizationAppleIDButton, context: Context) {
            context.coordinator.action = action
            button.accessibilityIdentifier = identifier
            button.isEnabled = context.environment.isEnabled
        }
    }
}

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
    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body:some View {
        Form {
            if !deletion.showsProgressAction {
                Section(copy("服務帳號")) {
                    LiteAppleSignInButton(identifier: "lite.account.signIn") {
                        Task { await identity.signIn() }
                    }
                    Button(copy("核對未完成登入")) {Task{await identity.retry()}}
                    Button(copy("登出服務帳號")) {Task{await identity.signOut()}}
                    Text(copy(identity.message)).font(.footnote)
                }.disabled(identity.busy || deletion.busy)
                if LiteNativeBuild.showsOwnerReview {
                    LiteOwnerReviewExport(identity: identity)
                        .disabled(identity.busy || deletion.busy)
                }
                Section(copy("刪除帳號")) {
                    Text(copy("刪除服務帳號時，也會撤回與這個帳號連結的研究參與。我們會停止雲端服務，撤銷 Apple 登入授權，並核對可對應的研究影像、深度及標註。"))
                    Text(copy("目前驗收版會分別回報各項處理進度，完整帳號資料清理仍在驗證中；提出要求不代表資料已刪除。"))
                        .foregroundStyle(.secondary)
                    Button(copy("刪除帳號…"),role:.destructive){confirming=true}
                        .disabled(identity.busy || deletion.busy)
                        .accessibilityIdentifier("accountDeletionConfirm")
                }
            }
            if let original = deletion.record, original.researchWithdrawalConfirmed != true && original.receipt?.research_withdrawal != "requested" {
                Button(copy("確認原刪帳要求也撤回研究…"), role:.destructive){confirming=true}
                    .disabled(deletion.busy)
            }
            Section(copy("刪帳進度")) {
                if deletion.busy{ProgressView(copy("正在核對原要求…"))}
                Text(deletion.presentationMessage(locale: locale)).accessibilityIdentifier("accountDeletionStatus")
                if let r=deletion.record?.receipt {
                    LabeledContent(copy("研究撤回"),value:r.research_withdrawal == "requested" ? copy("已受理，清理待核對") : copy("尚未確認受理"))
                    LabeledContent(copy("Apple 授權"),value:r.apple_revocation=="confirmed" ? copy("撤銷已確認"):copy("處理／核對中"))
                    if let progress=r.disposition {
                        LabeledContent(copy("目前服務結果"),value:progress.serviceCopy.render(locale: locale))
                        LabeledContent(copy("Apple 憑證金鑰"),value:copy(progress.keyText))
                        Text(copy("封存副本、舊服務可對應的資料、歷史版本及備份仍待核對。"))
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        LabeledContent(copy("帳號資料清理"),value:copy("尚未開始"))
                    }
                    LabeledContent(copy("帳號刪除"),value:copy("尚未完成"))
                }
                if deletion.showsProgressAction {
                    Button(copy("核對原刪帳進度")) {Task{await deletion.retry()}}
                        .disabled(deletion.busy).accessibilityIdentifier("accountDeletionRetry")
                }
                if let pending=deletion.record,pending.receipt==nil,pending.researchWithdrawalConfirmed == true,!deletion.needsReview {
                    Button(copy("核對身分並繼續原刪帳要求")) {Task{await deletion.reauthenticate()}}
                        .disabled(deletion.busy || identity.busy).accessibilityIdentifier("accountDeletionReauthenticate")
                    Text(copy("只有按此按鈕才會開啟 Apple 身分核對；請使用原帳號。這不會恢復雲端服務、研究上傳或訂閱額度。"))
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let researchLink, let record = deletion.record, record.receipt?.research_withdrawal == "requested" {
                LiteResearchLinkSection(link: researchLink, record: record)
            }
            Section(copy("保留與訂閱")) {
                Text(copy("手機照片與紀錄保留。如果只想退出研究，可在「資料與撤回」單獨撤回並保留服務帳號。"))
                Text(copy("刪除帳號不等於取消 Apple 訂閱。如有訂閱，請另至 Apple 訂閱管理確認。"))
                LiteAppleSubscriptionLink()
            }
        }.navigationTitle(copy("帳號與刪除"))
        .task{await deletion.reload()}
        .alert(copy("確認提出刪帳要求？"),isPresented:$confirming){
            Button(copy("確認刪帳並撤回研究"),role:.destructive){Task{await deletion.requestConfirmed()}}
            Button(copy("取消"),role:.cancel){}
        }message:{Text(copy("確認後，這部手機會先暫停研究上傳，再提出刪帳與研究撤回。影像、深度、標註及封存副本依可確認的範圍清理。手機紀錄保留；如有 Apple 訂閱，請另行取消。"))}
    }
}

extension LiteOwnerReviewReference: Transferable {
    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .json) { try JSONEncoder().encode($0) }
    }

    var exportFilename: String {
        operation == nil ? "WoundAILite-owner-installation" : "WoundAILite-owner-request"
    }
}

private struct LiteOwnerReviewExport: View {
    @ObservedObject var identity: LiteAccountIdentityStore
    @Environment(\.locale) private var locale
    @State private var document: LiteOwnerReviewReference?
    @State private var exporting = false
    @State private var preparing = false
    @State private var notice = ""
    @State private var preparation: Task<Void, Never>?

    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }

    var body: some View {
        Section(copy("本人內測核對")) {
            Text(copy("登入前可匯出安裝摘要。收到待核對提示後再匯出一次，交給自己的 Mac；這份檔案包含這次安裝及登入的識別碼。"))
                .font(.footnote)
            Button(copy("匯出本人核對資料")) {
                preparing = true
                notice = ""
                preparation = Task { @MainActor in
                    defer { preparing = false }
                    do {
                        let reference = try await identity.ownerReviewReference()
                        try Task.checkCancellation()
                        document = reference
                        exporting = true
                    } catch {
                        if !Task.isCancelled { notice = "目前無法匯出。請先核對裝置登記及登入進度。" }
                    }
                }
            }
            .disabled(preparing || exporting)
            .accessibilityIdentifier("liteOwnerReviewExport")
            if !notice.isEmpty { Text(copy(notice)).font(.footnote) }
        }
        .fileExporter(isPresented: $exporting, item: document, contentTypes: [.json],
                      defaultFilename: document?.exportFilename) { result in
            document = nil
            switch result {
            case .success: notice = "本人核對資料已匯出。請將檔案保存在您的私有位置。"
            case .failure(let error):
                notice = (error as NSError).code == NSUserCancelledError ? "" : "匯出未完成，請重新選擇儲存位置。"
            }
        } onCancellation: {
            document = nil
            notice = ""
        }
        .onChange(of: identity.generation) { _, _ in clear() }
        .onDisappear { clear() }
    }

    private func clear() {
        preparation?.cancel()
        exporting = false
        document = nil
        notice = ""
    }
}

private struct LiteResearchLinkSection: View {
    @ObservedObject var link: LiteResearchDeletionLink
    let record: LiteAccountDeletionCheckpoint
    @State private var confirming = false

    @Environment(\.locale) private var locale
    private func copy(_ source: String) -> String { LiteServiceCopy.text(source, locale: locale) }
    var body: some View {
        Section(copy("核對本機研究登記")) {
            Text(copy(link.message)).accessibilityIdentifier("researchLinkProgress")
            if !link.confirmed {
                Button(copy("核對這部手機的研究登記…")) { confirming = true }
                    .disabled(link.busy)
                if link.busy { ProgressView(copy("核對中…")) }
            }
        }
        .task(id: record.operation) { await link.reload(record) }
        .alert(copy("將本機原研究登記連結至這筆刪帳要求？"), isPresented: $confirming) {
            Button(copy("確認連結並核對")) { Task { await link.confirm(record) } }
            Button(copy("返回"), role: .cancel) {}
        } message: {
            Text(copy("兩個服務會各自核對這部手機原有的登記，讓這筆撤回能找到對應的研究資料。只傳核對所需的識別資料，手機照片與深度會保留。"))
        }
    }
}
