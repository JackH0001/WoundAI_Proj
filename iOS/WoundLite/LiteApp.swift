import SwiftUI
import AVFoundation

/**
 WoundLite——民眾版免貼紙傷口量測（App Store 一般用戶）。

 ## 與醫療版（WoundMeasurementApp）的關係

 同一個 repo、同一份 Core（`DepthAreaEstimator`／`DepthCapture`／`MaskTrace`／
 `LocalImageStore`／`BackendClient`）、獨立 target 與 bundle id。醫療版是這裡的
 對照驗證台：貼紙 vs 深度的 phantom 誤差表（2026-08-18 實測）決定了本版的設計：

 - **表面積為主數字**：投影面積隨拍攝角度變（cosθ），民眾手持晃動數字就跟著晃；
   表面積傾角不變且在肢體曲面上才是皮膚實際面積。平滑＋品質閘是前提。
 - **單一中心傷口**：只取最靠近畫面中央的輪廓，與相機中央對焦框同一套構圖語言。
 - **品質不足就不給數字**：比值閘（表面/投影 >1.25）、攝距、傾角、覆蓋率——
   寧可請使用者重拍，也不給一個看起來煞有介事的錯數字。

 ## 圈選與研究參與

 本機手動圈選可直接使用。研究上傳須由使用者在設定中閱讀說明並同意。
 含個人服務的組建另有雲端辨識入口，由個人服務的授權流程處理。
 */
@main
struct WoundLiteApp: App {
    private static let preparePDFSharing: Void = { LitePDFShare.purgeAfterLaunch() }()
    init() { _ = Self.preparePDFSharing }
    var body: some Scene {
        WindowGroup {
            #if WOUND_LITE_NATIVE_SANDBOX
            LiteNativeCandidateRoot()
            #else
            LiteRootView()
            #endif
        }
    }
}

/// Device-local pause only. It does not assert that an anonymous research
/// installation belongs to a service account or that remote media was removed.
struct LiteAccountResearchPause {
    let directory: URL
    static let standard = Self(directory: FileManager.default.urls(for:.applicationSupportDirectory,
        in:.userDomainMask)[0].appendingPathComponent("lite-account-research-pause"))

    private func file(for server: String) throws -> URL {
        let origin = try LiteWithdrawalJournal.origin(server)
        return directory.appendingPathComponent(LiteServiceBytes.hash(Data(origin.utf8)))
    }

    private func marker(_ server: String) throws -> Data? {
        let file = try file(for:server)
        do { return try Data(contentsOf:file) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
    }

    func blocks(_ server: String) -> Bool {
        do { return try marker(server) != nil }
        catch { return true }
    }

    func pause(_ server: String) throws {
        let value = Data("account-deletion-device-upload-pause-v1".utf8)
        if let prior = try marker(server) {
            guard prior == value else { throw LiteDeletionFailure.invalid }
            return
        }
        try HealthDataFiles.write(value, to:file(for:server))
        guard try marker(server) == value else { throw LiteDeletionFailure.invalid }
    }
}

/// 民眾版偏好設定。獨立於醫療版（不同 bundle 沙盒，天然隔離）。
enum LitePrefs {
    private static let d = UserDefaults.standard
    static let researchChoiceKey = "lite_research_consent"
    // The server contract and signed historical receipts have their own version.
    // This marker records which additional on-device notice the user reviewed.
    static let noticeKey = "lite_research_notice_version"
    static let noticeVersion = "2026-11-03.1"
    static func reviewedChoice(_ choice: Bool?, notice: String?) -> Bool? {
        choice == true && notice != noticeVersion ? nil : choice
    }
    static var researchPausedForAccountDeletion: Bool {
        LiteAccountResearchPause.standard.blocks(AppSettings.backendURL())
    }
    static func pauseResearchForAccountDeletion() throws {
        try LiteAccountResearchPause.standard.pause(AppSettings.backendURL())
    }
    /// nil means the current notice has no recorded choice. Research requires explicit acceptance in Settings.
    static var researchConsent: Bool? {
        get {
            guard !researchPausedForAccountDeletion, !LiteWithdrawalJournal.standard.blocksUploads else { return false }
            // An old acceptance must not silently authorize the revised disclosure.
            let choice = LiteBackendConfiguration.consentChoice(
                d.object(forKey: researchChoiceKey) as? Bool,
                savedVersion: d.string(forKey: "lite_consent_version"), requiredVersion: consentVersion,
                savedOrigin: d.string(forKey: "lite_consent_origin"), currentOrigin: AppSettings.backendURL())
            return reviewedChoice(choice, notice: d.string(forKey: noticeKey))
        }
        set {
            if newValue == true && (researchPausedForAccountDeletion || LiteWithdrawalJournal.standard.blocksUploads) { return }
            d.set(newValue == nil ? nil : consentVersion, forKey: "lite_consent_version")
            d.set(newValue == nil ? nil : AppSettings.backendURL(), forKey: "lite_consent_origin")
            d.set(newValue == true ? noticeVersion : nil, forKey: noticeKey)
            d.set(newValue, forKey: researchChoiceKey)
        }
    }
    /// Research upload contract. Changes to authorized use require a new server-accepted version.
    static let consentVersion = "2026-10-16.1"

    /// Legacy anonymous UUID, retained for old records/support only.
    /// New uploads use the server-assigned App Attest installation; never overwrite old bindings.
    static var anonId: String {
        if let v = d.string(forKey: "lite_anon_id"), !v.isEmpty { return v }
        let v = UUID().uuidString.lowercased()
        d.set(v, forKey: "lite_anon_id")
        return v
    }
}

enum LiteRootTab: Hashable { case measure, history, settings }

private struct LiteRearLiDARKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var liteRearLiDARAvailable: Bool {
        get { self[LiteRearLiDARKey.self] }
        set { self[LiteRearLiDARKey.self] = newValue }
    }
}

struct LiteRootView: View {
    @StateObject private var store = LiteStore()
    @Environment(\.scenePhase) private var scenePhase
    private let retryTimer = Timer.publish(every: 5, on: .main, in: .common).autoconnect()
    @State private var resumingResearch = false

    @MainActor private func resumeResearch() async {
        guard !resumingResearch else { return }
        resumingResearch = true; defer { resumingResearch = false }
        let withdrawal = LiteWithdrawalModel.shared
        if let record = withdrawal.record, record.phase == .pending,
           let due = record.retryAt, due <= Date() {
            await withdrawal.request(server: record.server)
        }
        if let delivery = nativeSession?.researchDelivery, LitePrefs.researchConsent == true {
            let due = store.records.filter {
                $0.cloudSync == nil && ($0.researchCapture?.retryAt.map { $0 <= store.revisionNow } ?? false)
            }
            for record in due { await delivery.resume(record.id, store: store) }
        }
        await store.resumePendingRevisions()
    }
    @State private var selectedTab: LiteRootTab = .measure
    @State private var savedRecordToOpen: String?
    /// Hardware gates new capture only; records and privacy controls stay accessible.
    let lidarOK: Bool
    let accountManagement:LiteAccountManagementView?
    let nativeSession:LiteNativeSession?
    let nativeSetupNotice: LiteNativeSetupNotice?

    init(lidarAvailable: Bool = AVCaptureDevice.default(.builtInLiDARDepthCamera,
                                                       for: .video, position: .back) != nil,
         accountManagement:LiteAccountManagementView?=nil,
         nativeSession:LiteNativeSession?=nil,
         nativeSetupNotice: LiteNativeSetupNotice? = nil) {
        lidarOK = lidarAvailable
        self.accountManagement=accountManagement
        self.nativeSession=nativeSession
        self.nativeSetupNotice=nativeSetupNotice
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            Group {
                if lidarOK {
                    LiteMeasureView(store: store, nativeSession: nativeSession) { id in
                        savedRecordToOpen = id
                        selectedTab = .history
                    }
                }
                else { lidarGate }
            }
            .tag(LiteRootTab.measure)
            .tabItem { Label(LiteServiceCopy.text("量測"), systemImage: "camera.metering.center.weighted") }
            LiteHistoryView(store: store, recordToOpen: $savedRecordToOpen, nativeSession:nativeSession)
                .tag(LiteRootTab.history)
                .tabItem { Label(LiteServiceCopy.text("紀錄"), systemImage: "chart.line.uptrend.xyaxis") }
            LiteSettingsView(accountManagement:accountManagement,nativeSession:nativeSession,
                             nativeSetupNotice:nativeSetupNotice)
                .tag(LiteRootTab.settings)
                .tabItem { Label(LiteServiceCopy.text("設定"), systemImage: "gearshape") }
        }
        .environment(\.liteRearLiDARAvailable, lidarOK)
        .background {
            if let nativeSession { LitePurchaseObservation(session: nativeSession) }
        }
        .task { await LiteReminderModel.shared.refresh(); await resumeResearch() }
        .onChange(of: scenePhase) { _, value in if value == .active { Task { await LiteReminderModel.shared.refresh(); await resumeResearch() } } }
        .onReceive(retryTimer) { _ in if scenePhase == .active { Task { await resumeResearch() } } }
    }

    private var lidarGate: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    Image(systemName: "camera.metering.unknown")
                        .font(.system(size: 48)).foregroundStyle(.secondary)
                    Text(LiteServiceCopy.text("此裝置無法拍攝深度量測")).font(.title3.bold())
                    Text(LiteServiceCopy.text("本裝置未偵測到可用的 LiDAR 深度相機，無法拍攝新的傷口面積量測。"))
                        .foregroundStyle(.secondary)
                    Text(LiteServiceCopy.text("您仍可查看此裝置已有的紀錄、設定、隱私說明與資料撤回。"))
                    Button { selectedTab = .history } label: {
                        Label(LiteServiceCopy.text("查看既有紀錄"), systemImage: "chart.line.uptrend.xyaxis")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    Button { selectedTab = .settings } label: {
                        Label(LiteServiceCopy.text("設定與資料撤回"), systemImage: "gearshape")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    NavigationLink(LiteServiceCopy.text("量測精確度與限制")) { LiteMeasurementInfoView() }
                }
                .multilineTextAlignment(.center)
                .padding(24)
            }
            .navigationTitle(LiteServiceCopy.text("量測"))
        }
    }
}
