import SwiftUI

/// Optional research consent. Personal service recognition has its own explicit send action.
struct LiteConsentView: View {
    @Environment(\.locale) private var locale
    private var copy: LiteResearchCopy { LiteResearchCopy(locale: locale) }
    let onChoose: (Bool) -> Void
    var renewingUnsentWithdrawal = false
    var renewingCompletedWithdrawal = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(copy.text(LiteNativeBuild.includesService ? "是否同意上傳研究資料？" : "選擇您的量測方式")).font(.title2).bold()
                        .accessibilityIdentifier("lite.research.consent.title")
                    if renewingUnsentWithdrawal {
                        Text(copy.text("您正在重新同意研究上傳。僅在確認撤回尚未送出、此服務沒有已登記身分或綁定紀錄後，才會取消這次未送出的撤回。原撤回與本次選擇會保留；舊服務紀錄不會移轉或重傳。"))
                            .font(.subheadline)
                    }
                    if renewingCompletedWithdrawal {
                        Text(copy.text("先前撤回已完成，已清除的雲端影像與深度不會恢復。這次只重新同意之後新拍攝的資料上傳；舊紀錄、舊待傳及未上傳的本機照片都不會自動補送。沿用原裝置身分，並保留撤回與本次同意的稽核紀錄。"))
                            .font(.subheadline)
                        Text(copy.text("必須取得雲端重新加入的確認，才會開啟研究上傳；中斷時保留同一次要求供核對。"))
                            .font(.footnote)
                    }

                    box(title: LiteNativeBuild.includesService ? "同意研究上傳" : "🔬 同意研究上傳（啟用自動辨識）", tint: .blue, lines: [
                        LiteNativeBuild.includesService ? "您可自行圈選，或登入個人服務後明確送出照片供辨識。同意研究上傳會讓符合研究條件的新紀錄進入研究送件流程。" : "拍攝後由雲端 AI 自動圈出傷口，再由您確認或修改。",
                        "研究資料包括照片、量測估算、傷口分組與側別部位。若有 AI 圈選，即使您沒有修邊，也可保存原始輪廓。您確認或修改的輪廓另有紀錄。",
                        "若拍到 LiDAR 深度，會一併傳送整張深度圖和相機參數。圈選外的物品也可能留下距離資訊。請避開臉部、姓名與證件。隨機安裝代碼可連結同一安裝的歷次資料。",
                        "已送出的研究照片若有修訂，會同步圈選與量測資料。重新開啟後，原同意期間仍在等候的研究送件也可能續傳。",
                        "用途：改進傷口辨識與 3D 量測精度的研究與模型訓練。",
                        "本版使用獨立 WoundAILite 服務；舊服務的影像與紀錄不會自動轉移。切換服務後須重新選擇是否上傳。",
                        "可在設定關閉未來上傳；另可要求撤回研究並清除本安裝目前可見的雲端影像與深度。本機紀錄保留；備份、歷史版本、研究標註與既有模型的刪除仍待驗證。",
                    ])

                    box(title: LiteNativeBuild.includesService ? "不同意研究上傳" : "📴 不同意（完全離線）", tint: .green, lines: [
                        LiteNativeBuild.includesService ? "拍攝後，照片及深度加密存於本機。若您在量測頁選擇個人雲端辨識，僅選取的照片會送往服務，深度留在本機。" : "照片與深度資料全部只留在您的手機（加密儲存）。",
                        LiteNativeBuild.includesService ? "您仍可保存傷口紀錄與分享時間軸 PDF。面積估算在裝置上完成。" : "傷口輪廓改由您手動圈選，量測計算全在裝置上完成。",
                        "之後可在設定重新選擇；若已撤回並清除雲端資料，須另行閱讀並完成重新同意。",
                    ])

                    if LiteNativeBuild.includesService {
                        Text(copy.text("您可單獨撤回研究並保留服務帳號。若確認刪除服務帳號，會同時提出與該帳號連結的研究撤回；清理進度須另外核對。"))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Text(copy.text(LiteNativeBuild.includesService ? "這項選擇只決定研究上傳。個人雲端辨識另行登入，由您決定是否送出照片。" : "兩種方式的量測數學完全相同；差別只在輪廓怎麼來、資料去不去雲端。"))
                        .font(.footnote).foregroundStyle(.secondary)

                    LitePrivacyLink()

                    Button {
                        onChoose(true)
                    } label: {
                        Text(copy.text(renewingCompletedWithdrawal ? "重新同意：只啟用之後的新上傳" : renewingUnsentWithdrawal ? "重新同意並取消尚未送出的撤回" : LiteNativeBuild.includesService ? "同意研究上傳" : "同意研究上傳，使用自動辨識"))
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("lite.research.agree")

                    Button {
                        onChoose(false)
                    } label: {
                        Text(copy.text(declineTitle))
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("lite.research.decline")
                }
                .padding()
            }
            .navigationTitle("WoundAILite")
        }
    }

    private var declineTitle: String {
        if renewingCompletedWithdrawal {
            return LiteNativeBuild.includesService ? "維持研究撤回" : "暫不開啟上傳，繼續離線使用"
        }
        if renewingUnsentWithdrawal {
            return LiteNativeBuild.includesService ? "保留研究撤回要求" : "保留撤回，繼續離線使用"
        }
        return LiteNativeBuild.includesService ? "不同意研究上傳，繼續使用" : "不同意，完全離線手動圈選"
    }

    private func box(title: String, tint: Color, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(copy.text(title)).font(.headline)
            ForEach(lines, id: \.self) { l in
                Text("・" + copy.text(l)).font(.subheadline)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.08))
        .cornerRadius(10)
    }
}

/// 設定：研究同意開關＋拍攝要領。
/// 2026-08-19 移除「進階（開發測試）」帳密區——雲端辨識已切換到匿名
/// `lite/segment` 端點，民眾版不再有任何帳號概念（交辦清單第 5 項）。
struct LiteSettingsView: View {
    @Environment(\.locale) private var locale
    private var copy: LiteResearchCopy { LiteResearchCopy(locale: locale) }
    /// Explicit candidate composition; ordinary shipping startup leaves this nil.
    var accountManagement:LiteAccountManagementView?=nil
    var nativeSession:LiteNativeSession?=nil
    var nativeSetupNotice: LiteNativeSetupNotice? = nil
    private var installedVersion: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? copy.text("未知")
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? copy.text("未知")
        return "\(version)（\(build)）"
    }
    @AppStorage(LitePrefs.researchChoiceKey) private var storedChoice: Bool?
    @AppStorage(LitePrefs.noticeKey) private var reviewedNotice: String?
    @State private var reviewingResearchChoice = false
    private var consent: Bool {
        storedChoice == true && reviewedNotice == LitePrefs.noticeVersion && LitePrefs.researchConsent == true
    }
    @StateObject private var withdrawal = LiteWithdrawalModel.shared
    @State private var confirmWithdrawal = false
    @State private var reviewRenewedConsent = false
    @State private var checkingConnection = false
    @State private var connectionMessage: String?
    @State private var mayRepairRegistration = false
    @State private var confirmRegistrationRepair = false

    @MainActor private static func recoveryAllowed(server: String) throws -> Bool {
        guard LitePrefs.researchConsent == true, !LiteWithdrawalJournal.standard.blocksUploads,
              AppSettings.backendURL() == server else { return false }
        let store = LiteStore()
        guard store.storageError == nil, !store.needsReload else { throw LiteAttestFailure.invalidState }
        return try !LiteWithdrawalModel.hasBoundRecords(store.records, server: server)
    }

    @MainActor private func checkConnection(repair: Bool = false) async {
        guard !checkingConnection, LitePrefs.researchConsent == true, !withdrawal.blocksUploads else { return }
        checkingConnection = true; defer { checkingConnection = false }
        mayRepairRegistration = false
        let server = AppSettings.backendURL()
        do {
            guard let origin = URL(string: server) else { throw LiteAttestFailure.invalidConfiguration }
            let client = try await LiteAttestClients.shared.client(origin: origin, audience: "woundlite-research-v1")
            if repair {
                _ = try await client.recoverUnregisteredIdentity(allowRecovery: {
                    try await Self.recoveryAllowed(server: server)
                })
            }
            _ = try await client.registeredInstallation()
            connectionMessage = "裝置登記已確認。本次沒有傳送照片；影像與深度仍須逐筆取得雲端保存確認。"
        } catch {
            if error as? LiteAttestFailure == .registrationRejected {
                mayRepairRegistration = (try? Self.recoveryAllowed(server: server)) == true
            }
            if error as? LiteAttestFailure == .registrationRecoveryNotReady {
                connectionMessage = "原驗證仍在有效期間，未更換裝置身分。請稍後再核對，原紀錄保留。"
            } else {
                connectionMessage = (error as? LiteAttestFailure)?.captureFailureMessage
                    ?? "雲端登記尚未確認，已保留原身分與紀錄。請稍後再核對，不用重拍。"
            }
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section(copy.text("App 資訊")) {
                    LabeledContent("App", value: "WoundAILite")
                    LabeledContent(copy.text("版本（建置號）"), value: installedVersion)
                    NavigationLink(copy.text("操作指南")) {
                        LiteOperationGuide(version: installedVersion, personal: LiteNativeBuild.includesService)
                    }.accessibilityIdentifier("lite.help.open")
                        .textSelection(.enabled)
                    LitePrivacyLink()
                    LiteTermsLink()
                    if LiteNativeBuild.includesService { LiteAppleSubscriptionLink() }
                }
                if let accountManagement {
                    Section(LiteServiceCopy.text("服務帳號")) {
                        NavigationLink(LiteServiceCopy.text("帳號與刪除")) {accountManagement}
                    }
                }
                if let nativeSession {
                    Section(LiteServiceCopy.text("個人服務")) {
                        NavigationLink(LiteServiceCopy.text("服務登入、訂閱與刪帳")) { LiteNativeSessionView(session:nativeSession) }
                    }
                } else if let nativeSetupNotice {
                    Section(LiteServiceCopy.text("個人服務")) {
                        switch nativeSetupNotice {
                        case .missingConfiguration:
                            Text(copy.text("此測試版本的服務網址尚未設定完成。您仍可使用本機紀錄。"))
                        case .storageUnavailable(let retry):
                            Text(copy.text("服務資料暫時無法讀取。請解鎖裝置後再試。"))
                            Button(copy.text("重新準備服務入口"), action: retry)
                        }
                    }
                }
                #if DEBUG && targetEnvironment(simulator)
                if ProcessInfo.processInfo.arguments.contains("-lite-commerce-lab") {
                    Section(copy.text("內部驗證")) {
                        NavigationLink(copy.text("訂閱、廣告與固定收案（合成）")) { LiteCommerceLab() }
                    }
                }
                #endif
                Section(copy.text("研究上傳")) {
                    Toggle(copy.text(LiteNativeBuild.includesService ? "上傳資料供研究" : "上傳去識別資料供研究（啟用自動辨識）"), isOn: Binding(
                        get: { consent },
                        set: { requested in
                            if requested { reviewingResearchChoice = true }
                            else { LitePrefs.researchConsent = false }
                        }))
                        .id(consent)
                        .accessibilityIdentifier("lite.research.toggle")
                        .disabled(LitePrefs.researchPausedForAccountDeletion || withdrawal.blocksUploads || withdrawal.busy)
                    Text(copy.text(consent && !LitePrefs.researchPausedForAccountDeletion
                         ? "研究送件可包含照片、整張深度圖、原始 AI 圈選與後續修訂，以及量測估算與側別部位。可隨時關閉。"
                         : LiteNativeBuild.includesService ? "可在本機圈選，或登入後從量測頁選擇個人雲端辨識。個人服務和研究使用分開決定。" : "完全離線：照片與深度只留在手機，輪廓手動圈選。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    if LitePrefs.researchPausedForAccountDeletion {
                        Text(copy.text("因刪帳要求，這部手機已暫停研究上傳。請至帳號刪除頁核對已連結資料的撤回進度。"))
                            .font(.footnote)
                    } else if withdrawal.blocksUploads {
                        Text(copy.text("研究上傳已因撤回停用。請查看下方「資料與撤回」狀態；重試查核不會重新開啟研究上傳。"))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if !LiteNativeBuild.includesService && consent && !withdrawal.blocksUploads && !LitePrefs.researchPausedForAccountDeletion {
                    Section(copy.text("研究辨識額度")) { LiteResearchQuotaView() }
                }
                if let nativeSession {
                    Section(copy.text("個人雲端辨識")) {
                        NavigationLink(copy.text("查看個人帳號今日額度")) { LitePersonalBalanceView(session: nativeSession) }
                        Text(copy.text("免費追蹤 1 處，Plus 每帳戶最多同時追蹤 5 處。每日個人辨識總額為免費 2 次、Plus 10 次。本機圈選、保存及分享時間軸 PDF 不扣辨識次數。"))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section(copy.text("雲端連線核對")) {
                    #if DEBUG
                    Text(copy.text("此為本機開發簽章版，目前雲端只接受 TestFlight 或 App Store 版本。可使用本機量測與保存；雲端驗收須改用符合簽章要求的版本。"))
                        .font(.footnote)
                    #else
                    Button(copy.text(checkingConnection ? "核對中…" : "核對裝置登記（不傳照片）")) {
                        Task { await checkConnection() }
                    }
                    .disabled(checkingConnection || LitePrefs.researchConsent != true || withdrawal.blocksUploads || withdrawal.busy)
                    if let connectionMessage { Text(copy.text(connectionMessage)).font(.footnote) }
                    if mayRepairRegistration {
                        Button(copy.text("修復尚未完成的裝置登記")) { confirmRegistrationRepair = true }
                            .disabled(checkingConnection || LitePrefs.researchConsent != true || withdrawal.blocksUploads || withdrawal.busy)
                            .confirmationDialog(copy.text("核對並修復未完成的登記？"), isPresented: $confirmRegistrationRepair,
                                                titleVisibility: .visible) {
                                Button(copy.text("核對並修復")) { Task { await checkConnection(repair: true) } }
                                Button(copy.text("取消"), role: .cancel) { }
                            } message: {
                                Text(copy.text("先查詢原登記是否已成功；僅伺服器確認尚未完成登記、目前服務沒有綁定紀錄且研究上傳仍獲同意時，才建立新的驗證。原驗證與處置稽核會保留，不刪紀錄、不恢復已撤回資料，也不傳送照片。"))
                            }
                    }
                    #endif
                }
                Section(copy.text("拍攝要領")) {
                    Text(copy.text("・正對傷口拍攝（斜拍會造成面積誤差，App 會提醒）\n"
                         + "・距離 25–40 公分，等中央對焦框變綠再按快門\n"
                         + "・光線充足、避免反光與濕亮面\n"
                         + "・只拍傷口部位：請勿讓臉部、證件或可辨識個人的物品入鏡"
                         + "（尤其開啟研究上傳時）\n"
                         + "・小於 1 cm² 的傷口誤差較大，數字僅供趨勢參考"))
                        .font(.footnote)
                }
                Section(copy.text("量測結果怎麼讀")) {
                    Text(copy.text("「傷口面積」使用 LiDAR 深度與圈選輪廓估算皮膚表面積。角度、光線、深度品質與圈選都會影響結果。"
                         + "本 App 為健康參考工具，非醫療診斷；傷口有惡化跡象請就醫。"))
                        .font(.footnote)
                    NavigationLink(copy.text("精確度與限制")) { LiteMeasurementInfoView() }
                }
                Section(copy.text("資料與撤回")) {
                    Text(copy.text("手機紀錄、照片與深度資料設定排除系統備份。換機、刪除 App 或裝置遺失可能無法復原；研究上傳不等於個人備份。此設定不會清除過去已有的備份。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    Text(copy.text("關閉研究上傳只停止未來傳送，不會刪除已上傳資料。以下撤回適用於目前服務、此 App 保有可驗證身分的安裝；不刪除手機紀錄，也不代表其他服務或舊版匿名資料已刪除。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    if withdrawal.record != nil {
                        Text(copy.text(withdrawal.statusTitle))
                            .font(.subheadline).bold()
                    }
                    if let message = withdrawal.message {
                        Text(copy.text(message)).font(.footnote).textSelection(.enabled)
                    }
                    Text(copy.text("歷史版本、備份、既有研究標註及已訓練模型的處理尚未完成驗證，不能保證全部刪除。撤回後本安裝維持離線，不會自動建立新身分或恢復研究上傳。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    if withdrawal.mayReviewUnsentCancellation {
                        Button(copy.text("閱讀說明並重新同意研究上傳")) { reviewRenewedConsent = true }
                            .disabled(withdrawal.busy)
                    }
                    if withdrawal.mayReviewCompletedRenewal {
                        Button(copy.text("閱讀說明並重新加入研究")) { reviewRenewedConsent = true }
                            .disabled(withdrawal.busy)
                        Text(copy.text("重新加入只適用於之後的新拍攝，不會恢復已清除資料或補送舊紀錄。"))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button(copy.text(withdrawal.busy ? "處理中…" : (withdrawal.record == nil || withdrawal.cancelledBeforeSubmission || withdrawal.renewed ? "撤回研究並要求清除雲端媒體" : "重試／查核撤回")), role: .destructive) {
                        if withdrawal.record == nil || withdrawal.cancelledBeforeSubmission || withdrawal.renewed { confirmWithdrawal = true }
                        else { Task { await withdrawal.request(server: AppSettings.backendURL()) } }
                    }
                    .disabled(withdrawal.busy || withdrawal.finished)
                    Link(copy.text("聯絡資料處理支援"), destination: URL(string: "mailto:jack.hou@gmail.com")!)
                    .confirmationDialog(copy.text("撤回研究並要求清除雲端媒體？"), isPresented: $confirmWithdrawal,
                                        titleVisibility: .visible) {
                        Button(copy.text("確認撤回"), role: .destructive) {
                            LitePrefs.researchConsent = false
                            Task { await withdrawal.request(server: AppSettings.backendURL()) }
                        }
                        Button(copy.text("取消"), role: .cancel) {}
                    } message: {
                        Text(copy.text("先停止未來上傳，再送出撤回請求。既有影像與深度的可見媒體清除後無法復原；本機紀錄仍保留。若網路中斷，請回來重試查核。"))
                    }
                }
            }
            .navigationTitle(LiteServiceCopy.text("設定"))
            .sheet(isPresented: $reviewingResearchChoice) {
                LiteConsentView { agreed in
                    reviewingResearchChoice = false
                    if agreed && !withdrawal.busy { LitePrefs.researchConsent = true }
                }
            }
            .sheet(isPresented: $reviewRenewedConsent) {
                LiteConsentView(onChoose: { choice in
                    reviewRenewedConsent = false
                    guard choice else { return }
                    let server = AppSettings.backendURL()
                    Task {
                        let restored: Bool
                        if withdrawal.mayReviewCompletedRenewal {
                            restored = await withdrawal.reconsentCompletedWithdrawal(server: server)
                        } else {
                        restored = await withdrawal.reconsentUnsentWithdrawal(server: server,
                            consentVersion: LitePrefs.consentVersion, hasBoundRecords: {
                                // Reload on both sides of the asynchronous identity check.
                                // An unreadable index must never be treated as an empty one.
                                let store = LiteStore()
                                guard store.storageError == nil, !store.needsReload,
                                      AppSettings.backendURL() == server else { throw LiteWithdrawalFailure.invalidState }
                                return try LiteWithdrawalModel.hasBoundRecords(store.records, server: server)
                            })
                        }
                        if restored {
                            LitePrefs.researchConsent = true
                        }
                    }
                }, renewingUnsentWithdrawal: withdrawal.mayReviewUnsentCancellation,
                    renewingCompletedWithdrawal: withdrawal.mayReviewCompletedRenewal)
            }
        }
    }
}
