#if WOUND_INSTITUTION
import SwiftUI
import GoogleSignIn
import GoogleSignInSwift

@MainActor
final class InstitutionGoogleSession: ObservableObject {
    static let shared = InstitutionGoogleSession()
    @Published private(set) var email: String?
    @Published private(set) var busy = false
    @Published private(set) var ready = false
    @Published private(set) var message: String?
    private var provider: InstitutionRefreshingTokenProvider?
    private var generation: UInt64 = 0

    private let rememberedSubjectKey = "mmh.google.rememberedSubject"
    private var restoreAttempted = false

    func restoreIfNeeded() async {
        guard !ready, !busy, !restoreAttempted else { return }
        restoreAttempted = true
        guard GIDSignIn.sharedInstance.hasPreviousSignIn() else { return }
        await authenticate(presenting: nil)
    }

    func signIn(presenting: UIViewController) async {
        await authenticate(presenting: presenting)
    }

    private func authenticate(presenting: UIViewController?) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        await clearSession(forget: false)
        message = presenting == nil ? "正在恢復 Google 登入並確認機構入口…" : "正在登入 Google…"
        let attempt = generation
        guard let clientID = Bundle.main.object(forInfoDictionaryKey: "GIDClientID") as? String,
              let audience = Bundle.main.object(forInfoDictionaryKey: "GIDServerClientID") as? String,
              clientID.hasSuffix(".apps.googleusercontent.com"),
              audience.hasSuffix(".apps.googleusercontent.com"), clientID != audience else {
            message = InstitutionEntryError.unconfigured.localizedDescription; return
        }
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(clientID: clientID, serverClientID: audience)
        do {
            let user: GIDGoogleUser
            if let presenting { user = try await GIDSignIn.sharedInstance.signIn(withPresenting: presenting).user }
            else { user = try await GIDSignIn.sharedInstance.restorePreviousSignIn() }
            try Task.checkCancellation()
            guard attempt == generation, let subject = user.userID, !subject.isEmpty else {
                throw InstitutionEntryError.signInRequired
            }
            let boundUser = user
            let fresh = InstitutionRefreshingTokenProvider(subject: subject, audience: audience) {
                guard GIDSignIn.sharedInstance.currentUser?.userID == subject else { throw InstitutionEntryError.signInRequired }
                let updated = try await boundUser.refreshTokensIfNeeded()
                guard GIDSignIn.sharedInstance.currentUser?.userID == subject,
                      updated.userID == subject, let token = updated.idToken?.tokenString else {
                    throw InstitutionEntryError.signInRequired
                }
                return token
            }
            _ = try await fresh.freshToken()
            guard attempt == generation else { fresh.invalidate(); throw InstitutionEntryError.signInRequired }
            // Preserve the encrypted application credentials only for the same Google identity.
            if UserDefaults.standard.string(forKey: rememberedSubjectKey) != subject {
                AppSettings.clearCredentials()
            }
            provider = fresh
            email = user.profile?.email ?? "Google 帳號已登入"
            // OAuth can be tested before service IAP activation. Never send tokens to a placeholder.
            guard let configured = Bundle.main.object(forInfoDictionaryKey: "WoundAIInstitutionBackendURL") as? String,
                  !configured.isEmpty, let origin = URL(string: configured),
                  origin.host?.hasSuffix(".run.app") == true else {
                message = "Google 帳號已登入；機構入口尚未啟用，目前不能連接後端。"
                return
            }
            let transport = try InstitutionIAPTransport(origin: origin, audience: audience, subject: subject, provider: fresh)
            // An SDK login alone is not IAP acceptance. Read only, no App credentials.
            let probeURL = origin.appendingPathComponent("api/health").appending(queryItems: [URLQueryItem(name: "mmh_native_probe", value: "1")])
            let (data, response) = try await transport.send(URLRequest(url: probeURL))
            try InstitutionEntryHealth.validate(data, response: response, service: "woundai-backend-mmhps20261007")
            guard attempt == generation else { await transport.invalidate(); throw InstitutionEntryError.signInRequired }
            InstitutionTransportRegistry.shared.replace(with: transport)
            UserDefaults.standard.set(subject, forKey: rememberedSubjectKey)
            ready = true
            message = "Google 與機構入口驗證通過。原帳號可沿用已儲存的 WoundAI 登入資料；上傳狀態請查看紀錄。"
        } catch {
            await clearSession(forget: false)
            message = (error as? InstitutionEntryError)?.localizedDescription ?? "Google 登入未完成。若已取消可重新操作；請確認網路及機構帳號資格。"
        }
    }

    func signOut() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        await clearSession(forget: true)
        message = "已登出 Google 與此 App 的雲端帳號。本機紀錄已保留；瀏覽器需另行登出。"
    }

    private func clearSession(forget: Bool) async {
        generation &+= 1
        ready = false; email = nil
        provider?.invalidate(); provider = nil
        let old = InstitutionTransportRegistry.shared.replace(with: nil)
        await old?.invalidate()
        if forget {
            AppSettings.clearCredentials()
            UserDefaults.standard.removeObject(forKey: rememberedSubjectKey)
            GIDSignIn.sharedInstance.signOut()
        }
    }
}

struct InstitutionGoogleEntrySection: View {
    @ObservedObject private var entry = InstitutionGoogleSession.shared
    @EnvironmentObject var app: AppState
    let connectionBusy: Bool
    let resetApplicationLogin: () -> Void
    @State private var presenter: UIViewController?

    var body: some View {
        Section("機構 Google 入口") {
            Text("先用 Google 帳號通過機構門禁，再用 WoundAI 帳號確認工作權限。")
                .font(.footnote)
            DisclosureGroup("為什麼需要兩次登入？") {
                Text("① Google 帳號：確認您有權進入 MMH 私有服務。入口資格由機構管理員在 Google Cloud IAP 管理。")
                Text("② WoundAI 帳號：決定醫師確認、護理量測等操作權限，並記錄送件人。請使用配發的個人帳號；Google 登入不會自動取得醫師權限。")
                Text("這是兩層授權，不等同雙因素驗證。Google 入口問題請聯絡入口管理員；角色或 WoundAI 密碼問題請聯絡 WoundAI 帳號管理員。")
            }.font(.footnote)
            Text(entry.ready ? "Google 門禁：已驗證" : (entry.busy ? "Google 門禁：驗證中…" : "Google 門禁：尚未驗證"))
            if let email = entry.email {
                LabeledContent("Google 帳號", value: email)
                Button(entry.ready ? "登出 Google 與 WoundAI" : "清除 Google 登入並重試", role: .destructive) {
                    resetApplicationLogin(); app.identity = nil
                    Task { await entry.signOut() }
                }.disabled(entry.busy || connectionBusy)
            } else {
                GoogleSignInButton {
                    guard let presenter else { return }
                    resetApplicationLogin(); app.identity = nil
                    Task { await entry.signIn(presenting: presenter) }
                }.accessibilityIdentifier("MMHGoogleSignIn")
                .disabled(entry.busy || connectionBusy || presenter == nil)
            }
            if entry.busy { ProgressView("正在處理 Google 登入…") }
            if let message = entry.message { Text(message).font(.footnote) }
        }
        .task { await entry.restoreIfNeeded() }
        .background(InstitutionPresenter { presenter = $0 }.frame(width: 0, height: 0))
    }
}

private struct InstitutionPresenter: UIViewControllerRepresentable {
    let onReady: (UIViewController) -> Void
    func makeUIViewController(context: Context) -> UIViewController { Host(onReady: onReady) }
    func updateUIViewController(_ uiViewController: UIViewController, context: Context) {}
    private final class Host: UIViewController {
        let onReady: (UIViewController) -> Void
        init(onReady: @escaping (UIViewController) -> Void) { self.onReady = onReady; super.init(nibName: nil, bundle: nil) }
        required init?(coder: NSCoder) { fatalError("Not used") }
        override func viewDidAppear(_ animated: Bool) { super.viewDidAppear(animated); onReady(self) }
    }
}
#endif
