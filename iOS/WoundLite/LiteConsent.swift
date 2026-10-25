import SwiftUI

/**
 研究上傳同意（首啟必問；設定頁可改）。

 同意 ↔ 功能的對價要說清楚：同意＝啟用雲端自動辨識，代價是去識別資料上傳；
 不同意＝完全離線手動圈選。兩條路都是完整功能，不是「不同意就不能用」——
 App Store 審查（5.1.1 資料收集與儲存）也要求非必要資料收集不得綁架核心功能。
 */
struct LiteConsentView: View {
    let onChoose: (Bool) -> Void
    var renewingUnsentWithdrawal = false
    var renewingCompletedWithdrawal = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("選擇您的量測方式").font(.title2).bold()
                    if renewingUnsentWithdrawal {
                        Text("您正在重新同意研究上傳。僅在確認撤回尚未送出、此服務沒有已登記身分或綁定紀錄後，才會取消這次未送出的撤回。原撤回與本次選擇會保留；舊服務紀錄不會移轉或重傳。")
                            .font(.subheadline)
                    }
                    if renewingCompletedWithdrawal {
                        Text("先前撤回已完成，已清除的雲端影像與深度不會恢復。這次只重新同意之後新拍攝的資料上傳；舊紀錄、舊待傳及未上傳的本機照片都不會自動補送。沿用原裝置身分，並保留撤回與本次同意的稽核紀錄。")
                            .font(.subheadline)
                        Text("必須取得雲端重新加入的確認，才會開啟研究上傳；中斷時保留同一次要求供核對。")
                            .font(.footnote)
                    }

                    box(title: "🔬 同意研究上傳（啟用自動辨識）", tint: .blue, lines: [
                        "拍攝後由雲端 AI 自動圈出傷口，再由您確認或修改。",
                        "上傳傷口影像、LiDAR 深度圖與相機參數，以及您修改的輪廓、量測估算、傷口分組與側別部位。隨機安裝代碼會串聯同一安裝的資料；請勿拍入臉部、姓名或證件。",
                        "已上傳影像的紀錄修正圈選後，會同步修訂輪廓與量測資料；不重傳照片。舊紀錄若無雲端識別碼則只更新本機。",
                        "用途：改進傷口辨識與 3D 量測精度的研究與模型訓練。",
                        "本版使用獨立 WoundAILite 服務；舊服務的影像與紀錄不會自動轉移。切換服務後須重新選擇是否上傳。",
                        "可在設定關閉未來上傳；另可要求撤回研究並清除本安裝目前可見的雲端影像與深度。本機紀錄保留；備份、歷史版本、研究標註與既有模型的刪除仍待驗證。",
                    ])

                    box(title: "📴 不同意（完全離線）", tint: .green, lines: [
                        "照片與深度資料全部只留在您的手機（加密儲存）。",
                        "傷口輪廓改由您手動圈選，量測計算全在裝置上完成。",
                        "之後可在設定重新選擇；若已撤回並清除雲端資料，須另行閱讀並完成重新同意。",
                    ])

                    Text("兩種方式的量測數學完全相同；差別只在輪廓怎麼來、資料去不去雲端。")
                        .font(.footnote).foregroundStyle(.secondary)

                    Button {
                        onChoose(true)
                    } label: {
                        Text(renewingCompletedWithdrawal ? "重新同意：只啟用之後的新上傳" : renewingUnsentWithdrawal ? "重新同意並取消尚未送出的撤回" : "同意研究上傳，使用自動辨識")
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        onChoose(false)
                    } label: {
                        Text(renewingCompletedWithdrawal ? "暫不開啟上傳，繼續離線使用" : renewingUnsentWithdrawal ? "保留撤回，繼續離線使用" : "不同意，完全離線手動圈選")
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.bordered)
                }
                .padding()
            }
            .navigationTitle("WoundAILite")
        }
    }

    private func box(title: String, tint: Color, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            ForEach(lines, id: \.self) { l in
                Text("・" + l).font(.subheadline)
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
    private var installedVersion: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "未知"
        return "\(version)（\(build)）"
    }
    @State private var consent = LitePrefs.researchConsent ?? false
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
                Section("App 資訊") {
                    LabeledContent("App", value: "WoundAILite")
                    LabeledContent("版本（建置號）", value: installedVersion)
                        .textSelection(.enabled)
                }
                Section("研究上傳") {
                    Toggle("上傳去識別資料供研究（啟用自動辨識）", isOn: $consent)
                        .onChange(of: consent) { _, v in LitePrefs.researchConsent = v }
                        .disabled(withdrawal.blocksUploads || withdrawal.busy)
                    Text(consent
                         ? "拍攝影像、深度、修正輪廓、量測估算與傷口側別部位會上傳供辨識與研究；紀錄修改也會同步。可隨時關閉。"
                         : "完全離線：照片與深度只留在手機，輪廓手動圈選。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if withdrawal.blocksUploads {
                        Text("研究上傳已因撤回停用。請查看下方「資料與撤回」狀態；重試查核不會重新開啟研究上傳。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("個人雲端辨識") {
                    LiteQuotaView()
                    Text("免費額度用完仍可手動圈選、量測與保存本機紀錄。機構及多人個案管理請洽 WoundAI。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("雲端連線核對") {
                    #if DEBUG
                    Text("此為本機開發簽章版，目前雲端只接受 TestFlight 或 App Store 版本。可使用本機量測與保存；雲端驗收須改用符合簽章要求的版本。")
                        .font(.footnote)
                    #else
                    Button(checkingConnection ? "核對中…" : "核對裝置登記（不傳照片）") {
                        Task { await checkConnection() }
                    }
                    .disabled(checkingConnection || !consent || withdrawal.blocksUploads || withdrawal.busy)
                    if let connectionMessage { Text(connectionMessage).font(.footnote) }
                    if mayRepairRegistration {
                        Button("修復尚未完成的裝置登記") { confirmRegistrationRepair = true }
                            .disabled(checkingConnection || !consent || withdrawal.blocksUploads || withdrawal.busy)
                            .confirmationDialog("核對並修復未完成的登記？", isPresented: $confirmRegistrationRepair,
                                                titleVisibility: .visible) {
                                Button("核對並修復") { Task { await checkConnection(repair: true) } }
                                Button("取消", role: .cancel) { }
                            } message: {
                                Text("先查詢原登記是否已成功；僅伺服器確認尚未完成登記、目前服務沒有綁定紀錄且研究上傳仍獲同意時，才建立新的驗證。原驗證與處置稽核會保留，不刪紀錄、不恢復已撤回資料，也不傳送照片。")
                            }
                    }
                    #endif
                }
                Section("拍攝要領") {
                    Text("・正對傷口拍攝（斜拍會造成面積誤差，App 會提醒）\n"
                         + "・距離 25–40 公分，等中央對焦框變綠再按快門\n"
                         + "・光線充足、避免反光與濕亮面\n"
                         + "・只拍傷口部位：請勿讓臉部、證件或可辨識個人的物品入鏡"
                         + "（尤其開啟研究上傳時）\n"
                         + "・小於 1 cm² 的傷口誤差較大，數字僅供趨勢參考")
                        .font(.footnote)
                }
                Section("量測結果怎麼讀") {
                    Text("「傷口面積」使用 LiDAR 深度與圈選輪廓估算皮膚表面積。角度、光線、深度品質與圈選都會影響結果。"
                         + "本 App 為健康參考工具，非醫療診斷；傷口有惡化跡象請就醫。")
                        .font(.footnote)
                    NavigationLink("精確度與限制") { LiteMeasurementInfoView() }
                }
                Section("資料與撤回") {
                    Text("手機紀錄、照片與深度資料設定排除系統備份。換機、刪除 App 或裝置遺失可能無法復原；研究上傳不等於個人備份。此設定不會清除過去已有的備份。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("關閉研究上傳只停止未來傳送，不會刪除已上傳資料。以下撤回適用於目前服務、此 App 保有可驗證身分的安裝；不刪除手機紀錄，也不代表其他服務或舊版匿名資料已刪除。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if withdrawal.record != nil {
                        Text(withdrawal.statusTitle)
                            .font(.subheadline).bold()
                    }
                    if let message = withdrawal.message {
                        Text(message).font(.footnote).textSelection(.enabled)
                    }
                    Text("歷史版本、備份、既有研究標註及已訓練模型的處理尚未完成驗證，不能保證全部刪除。撤回後本安裝維持離線，不會自動建立新身分或恢復研究上傳。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if withdrawal.mayReviewUnsentCancellation {
                        Button("閱讀說明並重新同意研究上傳") { reviewRenewedConsent = true }
                            .disabled(withdrawal.busy)
                    }
                    if withdrawal.mayReviewCompletedRenewal {
                        Button("閱讀說明並重新加入研究") { reviewRenewedConsent = true }
                            .disabled(withdrawal.busy)
                        Text("重新加入只適用於之後的新拍攝，不會恢復已清除資料或補送舊紀錄。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button(withdrawal.busy ? "處理中…" : (withdrawal.record == nil || withdrawal.cancelledBeforeSubmission || withdrawal.renewed ? "撤回研究並要求清除雲端媒體" : "重試／查核撤回"), role: .destructive) {
                        if withdrawal.record == nil || withdrawal.cancelledBeforeSubmission || withdrawal.renewed { confirmWithdrawal = true }
                        else { Task { await withdrawal.request(server: AppSettings.backendURL()) } }
                    }
                    .disabled(withdrawal.busy || withdrawal.finished)
                    Link("聯絡資料處理支援", destination: URL(string: "mailto:jack.hou@gmail.com")!)
                    .confirmationDialog("撤回研究並要求清除雲端媒體？", isPresented: $confirmWithdrawal,
                                        titleVisibility: .visible) {
                        Button("確認撤回", role: .destructive) {
                            consent = false
                            Task { await withdrawal.request(server: AppSettings.backendURL()) }
                        }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("先停止未來上傳，再送出撤回請求。既有影像與深度的可見媒體清除後無法復原；本機紀錄仍保留。若網路中斷，請回來重試查核。")
                    }
                }
            }
            .navigationTitle("設定")
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
                            consent = LitePrefs.researchConsent == true
                        }
                    }
                }, renewingUnsentWithdrawal: withdrawal.mayReviewUnsentCancellation,
                    renewingCompletedWithdrawal: withdrawal.mayReviewCompletedRenewal)
            }
        }
    }
}
