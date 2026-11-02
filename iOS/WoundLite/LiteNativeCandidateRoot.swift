import SwiftUI
import UIKit

enum LiteNativeBuild {
    #if WOUND_LITE_NATIVE_SANDBOX
    static let includesService = true
    #else
    static let includesService = false
    #endif
    static var showsSavedRecordTest: Bool {
        includesService && Bundle.main.object(forInfoDictionaryKey: "LiteSavedRecordTestEnabled") as? String == "YES"
    }
}

enum LiteNativeSetupNotice {
    case missingConfiguration
    case storageUnavailable(retry: () -> Void)
}

#if WOUND_LITE_NATIVE_SANDBOX
/// The independently deployed candidate has its own App Attest scope. A new
/// deployment URL needs a source review. Never derive it from research settings.
enum LiteNativeCandidateBinding {
    static let address = "https://woundai-lite-native-sandbox-421209514056.asia-east1.run.app"
    static let audience = "woundlite-native-sandbox-v1"

    static func origin(bundleID: String?, configured: String?) throws -> URL {
        guard bundleID == "com.woundai.lite", configured == address, let url = URL(string: address)
        else { throw LiteAttestFailure.invalidConfiguration }
        return url
    }
}

@MainActor private final class CandidatePresentationWindow {
    weak var view: UIView?
}

private struct CandidateWindowProbe: UIViewRepresentable {
    let source: CandidatePresentationWindow
    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        source.view = view
        return view
    }
    func updateUIView(_ view: UIView, context: Context) {}
}

@MainActor private final class NativeCandidateEntry: ObservableObject {
    let presentation = CandidatePresentationWindow()
    @Published private(set) var session: LiteNativeSession?
    @Published private(set) var notice: LiteNativeSetupNotice?
    private var preparing = false

    func prepare() async {
        guard !preparing, session == nil else { return }
        preparing = true
        defer { preparing = false }
        do {
            let origin = try LiteNativeCandidateBinding.origin(bundleID: Bundle.main.bundleIdentifier,
                configured: Bundle.main.object(forInfoDictionaryKey: "LiteNativeSandboxOrigin") as? String)
            let client = try await LiteAttestClients.shared.client(origin: origin, audience: LiteNativeCandidateBinding.audience)
            try Task.checkCancellation()
            let support = try FileManager.default.url(for: .applicationSupportDirectory,
                in: .userDomainMask, appropriateFor: nil, create: false)
            session = try LiteNativeSession.sandbox(client: client, window: { [presentation] in presentation.view?.window },
                directory: support.appendingPathComponent("lite-native-sandbox-v1", isDirectory: true))
            notice = nil
        } catch LiteAttestFailure.invalidConfiguration {
            notice = .missingConfiguration
        } catch {
            notice = .storageUnavailable { [weak self] in Task { await self?.prepare() } }
        }
    }
}

/// Each scene supplies its own presentation window. Keep the same root view
/// while preparing so a current capture, open record, and tab selection survive.
struct LiteNativeCandidateRoot: View {
    @StateObject private var entry = NativeCandidateEntry()
    var body: some View {
        LiteRootView(nativeSession: entry.session, nativeSetupNotice: entry.notice)
        .background(CandidateWindowProbe(source: entry.presentation).frame(width: 0, height: 0))
        .task { await entry.prepare() }
    }
}
#endif
