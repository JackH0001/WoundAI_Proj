import SwiftUI
import UIKit

/**
 民眾版量測流程：拍照（LiDAR）→ 輪廓（雲端辨識或手動圈選）→ 深度幾何 → 結果。

 主數字＝LiDAR 輔助表面積估算；品質不足直接不給數字。
 所有重計算（影像正規化、深度估算）都在背景緒——醫療版 2026-08-18 的
 「畫面凍住 10 秒」教訓，這裡從第一天就不犯。
 */
@MainActor
final class LiteMeasureVM: ObservableObject {
    private(set) var native: LiteNativeSession?
    private let captureParticipation: @MainActor () -> LiteCaptureResearchScope?
    init(native: LiteNativeSession? = nil, captureParticipation: @escaping @MainActor () -> LiteCaptureResearchScope? = {
        try? LiteCaptureResearchScope.capture(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true)
    }) {
        self.native = native
        self.captureParticipation = captureParticipation
    }
    /// Candidate startup prepares one session per scene. A photo already in
    /// progress keeps the workflow selected at capture, even if setup finishes.
    func attachNativeSession(_ session: LiteNativeSession?) {
        if native == nil { native = session }
    }
    var usesServiceDraft: Bool { draftIdentity != nil }
    @Published private(set) var draft: LiteCaptureDraft?
    @Published private(set) var pendingCaptures: [LiteCaptureDraftSummary] = []
    @Published var showRecognitionAccount = false
    struct ManualEditor: Identifiable {
        let id = UUID()
        let image: UIImage
        let polygons: [[[Int]]]
        fileprivate let generation: Int
        fileprivate let draftID: String?
        fileprivate let revision: Int?
    }
    @Published var manualEditor: ManualEditor?
    @Published private(set) var manualSaveProblem: String?
    private var recognitionAwaitingTap: Int?
    private static let recognitionSignInPrompt = "請先開啟個人服務完成登入。回到量測後，可用同一張照片繼續辨識。"
    private static let pendingRegistrationPrompt = "請在個人服務點「續接裝置登記」，再回到這張照片。"
    private static let unreadableRegistrationPrompt = "暫時無法讀取這部裝置的登記。請解鎖後再點辨識，或先在手機圈選。"
    @Published private(set) var serviceResult: LiteServiceRecord?
    @Published private var verifiedTargets: LiteContourTargets?
    @Published var presentedTargets: LiteContourTargets?
    var availableTargets: LiteContourTargets? {
        guard let targets = verifiedTargets, let draft, savedId == nil,
              !draftNeedsReload, !serviceUnreadable, manualEditor == nil,
              targets.captureID == draft.id, targets.revision == draft.revision,
              targets.operation == serviceResult?.operation,
              targets.resultHash == serviceResult?.reply?.receipt?.result_sha256 else { return nil }
        return targets
    }
    @Published private(set) var draftNotice: String?
    @Published private(set) var serviceUnreadable = false
    @Published private(set) var draftNeedsReload = false
    @Published private(set) var resultApplicationPending = false
    private var draftIdentity: (UUID, Date)?
    private var draftResearchPin: LiteResearchCapturePin?
    private weak var recordStore: LiteStore?
    private var latestDraftRead = UUID()
    @Published var image: UIImage?
    /// The exact normalized JPEG used by the cloud and the local RGB-D sidecar.
    private(set) var captureJPEG: Data?
    @Published var polys: [[[Int]]] = []
    @Published var imageW = 0
    @Published var imageH = 0
    @Published var depth: DepthCapture?
    @Published var estimate: DepthAreaResult?
    @Published private var captureBusy = false
    @Published private var queryingRecognitionStatus = false
    var busy: Bool { captureBusy || queryingRecognitionStatus }
    var canEditOutline: Bool { !captureBusy && (!usesServiceDraft || draft != nil) }
    var activityHint: String {
        queryingRecognitionStatus && !captureBusy ? "正在核對個人雲端辨識…" : busyHint
    }
    @Published var busyHint = ""
    @Published var note: String?
    @Published var savedId: String?
    @Published var uploadMessage: String?
    /// 輪廓來源（存進紀錄）："manual" / "cloud"
    @Published var source = "manual"
    /// 這張影像在 lite/segment 落地後的 image_id（lay label 回收的綁定鍵）。
    private(set) var cloudImageId: String?
    private(set) var cloudBinding: LiteCloudBinding?
    var hasManualContour: Bool { userEdited }
    /// 雲端當時有沒有給出輪廓（lay label 的 source 語意：有→edited、無→manual）。
    private(set) var contourOrigin = LiteContourOrigin.manual
    private var cloudHadPolys = false
    /// 是否真的改過輪廓。AI 量測也可同步，但不可當成人工標註。
    private var userEdited = false
    private var gen = 0

    var captureButtonTitle: String {
        if image != nil { return "重新拍攝" }
        return LiteNativeBuild.includesService ? "拍攝測試目標" : "拍攝傷口"
    }

    /// Clear only after the durable index contains this capture. Pending sync belongs
    /// to the store, not this transient screen, and survives navigation/relaunch.
    func finishSavedCapture(in store: LiteStore) -> String? {
        guard !busy, let id = savedId,
              store.records.contains(where: { $0.id == id }) else { return nil }
        clearCapture()
        return id
    }

    private func clearCapture() {
        gen += 1
        draftNeedsReload = false; resultApplicationPending = false
        image = nil; captureJPEG = nil; depth = nil; polys = []
        imageW = 0; imageH = 0; estimate = nil
        busyHint = ""; note = nil; uploadMessage = nil; savedId = nil
        cloudImageId = nil; cloudBinding = nil; cloudHadPolys = false
        showRecognitionAccount = false
        draft = nil; serviceResult = nil; draftIdentity = nil; serviceUnreadable = false
        draftResearchPin = nil
        userEdited = false; source = "manual"; contourOrigin = .manual
    }

    /// 長邊 ≤2048＋方向烘進像素（與醫療版 `normalizeForBackend` 同規則；
    /// 座標空間正確性的前提，詳見該處註解）。
    nonisolated static func normalize(_ img: UIImage, maxDim: CGFloat = 2048) -> UIImage {
        let pw = CGFloat(img.cgImage?.width ?? Int(img.size.width))
        let ph = CGFloat(img.cgImage?.height ?? Int(img.size.height))
        let needRedraw = img.imageOrientation != .up || max(pw, ph) > maxDim
        if !needRedraw { return img }
        let dw = img.size.width, dh = img.size.height
        let sc = min(1, maxDim / max(dw, dh))
        let target = CGSize(width: (dw * sc).rounded(), height: (dh * sc).rounded())
        let fmt = UIGraphicsImageRendererFormat()
        fmt.scale = 1
        return UIGraphicsImageRenderer(size: target, format: fmt).image { _ in
            img.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    /// 拍照回來：先核對 RGB-D，再保存候選版草稿或走既有輪廓流程。
    /// - Returns: false＝立即開手動圈選；true＝留在量測頁選擇下一步。
    func ingest(_ raw: UIImage, depth d: DepthCapture?) async -> Bool {
        guard !busy else { return true }
        let native = self.native
        let researchScope = captureParticipation()
        gen += 1
        draftNeedsReload = false; resultApplicationPending = false
        captureBusy = true
        busyHint = "影像處理中…"
        defer { captureBusy = false }
        savedId = nil
        draft = nil; serviceResult = nil; draftNotice = nil; serviceUnreadable = false
        draftIdentity = native == nil ? nil : (UUID(), Date())
        draftResearchPin = nil
        if let researchScope, let delivery = native?.researchDelivery {
            draftResearchPin = await delivery.pin(researchScope)
        }
        uploadMessage = nil
        note = nil
        estimate = nil
        polys = []
        cloudImageId = nil
        cloudBinding = nil
        cloudHadPolys = false
        source = "manual"; contourOrigin = .manual
        userEdited = false
        let capture = await Task.detached(priority: .userInitiated) {
            let image = Self.normalize(raw)
            return (image, image.jpegData(compressionQuality: 0.92))
        }.value
        let work = capture.0
        captureJPEG = capture.1
        image = work
        imageW = work.cgImage?.width ?? Int(work.size.width)
        imageH = work.cgImage?.height ?? Int(work.size.height)
        depth = nil
        guard var dd = d else {
            note = "⚠ 這張照片沒有取得 LiDAR 深度，無法量測。請重拍（勿遮擋鏡頭旁的 LiDAR 感測器）。"
            return true   // 沒深度就不必進圈選——圈了也算不出來
        }

        // Check the delivered pair before binding a fresh camera map to the
        // normalized JPEG. Existing dimensions must not be silently replaced.
        guard let orientation = DepthCapture.exifOrientation(raw.imageOrientation),
              dd.sourceExifOrientation == Int(orientation.rawValue),
              dd.geometryIssue(imageW: imageW, imageH: imageH, requireOrientation: true) == nil else {
            note = "照片、深度或相機校正資料無法對應，未送出辨識或計算。請重新拍攝。"
            return true
        }
        dd.rgbWidth = imageW
        dd.rgbHeight = imageH
        depth = dd
        if let native {
            do {
                guard let jpeg = captureJPEG, let (id, date) = draftIdentity else { throw LiteServiceFailure.missingImage }
                draft = try await native.captures.create(jpeg: jpeg, depth: dd, id: id, now: date, researchPin: draftResearchPin)
                note = "照片與原始深度已保存在手機，尚未傳送。可選擇個人雲端辨識，或自行圈選後存入紀錄。"
            } catch {
                note = "照片與深度尚未完整保存，未送出辨識。請保留此畫面並重試保存。"
            }
            return true
        }

        // 輪廓來源優先序（2026-08-18 規劃）：
        //   ① 地端模型（成熟上線後把 .mlmodel 丟進 WoundLite/Models/ 即自動啟用，離線可用）
        //   ② 雲端辨識（研究同意＋連線設定）
        //   ③ 手動圈選（永遠可用的保底，也是①②結果的微調入口）
        if LiteLocalSeg.available {
            busyHint = "本地辨識中…"
            if let local = await LiteLocalSeg.segment(work) {
                let picked = Self.centerWound(local, w: imageW, h: imageH)
                if !picked.isEmpty {
                    polys = picked
                    source = "local"; contourOrigin = .local
                    note = "已由裝置端模型自動圈選（未連網）。可用「修正圈選」微調。"
                    await runEstimate()
                    return true
                }
            }
        }
        if !LiteNativeBuild.includesService, let researchScope,
           researchScope.permits(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true) {
            busyHint = "雲端辨識中…（約數秒）"
            switch await liteCloudSegment(work, depth: dd, researchScope: researchScope) {
            case .ok(let cloud, let stored, let route, let contract, let raw, let receipt):
                cloudHadPolys = !cloud.isEmpty
                // Debug 組建把路由與輪廓數直接掛在畫面上：route 說出後端走哪條路、
                // 「後端 N・解析 M」不一致＝客戶端解析問題——兩種病一眼分開。
                var dbg: String? = nil
                #if DEBUG
                dbg = "🔧 route=\(route ?? "—")・後端輪廓 \(raw)・解析成功 \(cloud.count)"
                #endif
                let picked = Self.centerWound(cloud, w: imageW, h: imageH)
                if !picked.isEmpty {
                    polys = picked
                    source = "cloud"
                    contourOrigin = .cloud(route: route, contract: contract)
                    var lines: [String] = []
                    if cloud.count > 1 {
                        lines.append("已自動取畫面中央的傷口（偵測到 \(cloud.count) 處，其餘忽略）。可用「修正圈選」微調。")
                    }
                    // 據實告知有沒有上傳——同意分流講給人聽才有意義（後端 stored 欄位）。
                    if let status = LiteStorageReceipt.message(imageStored: stored, receipt: receipt) {
                        lines.append(status + "可於設定停止未來上傳。")
                    }
                    if let d = dbg { lines.append(d) }
                    note = lines.isEmpty ? nil : lines.joined(separator: "\n")
                    await runEstimate()
                    return true
                }
                // 空輪廓時後端照樣落地（同意者）——難例正是研究最需要的樣本，
                // 而「有沒有上傳」必須據實告知，不因辨識失敗而略過。
                var msg = "雲端未辨識到傷口，請手動圈選。"
                if let status = LiteStorageReceipt.message(imageStored: stored, receipt: receipt) { msg += status }
                if let d = dbg { msg += "\n" + d }
                note = msg
                return false
            case .softFail(let message):
                // 429 配額／人臉退件：不是錯誤，是流程分流。訊息照後端的說法給。
                note = message
                return false
            case .hardFail:
                note = "雲端辨識未成功（連線或服務問題），請手動圈選傷口。"
                return false
            }
        }
        return false   // 未同意研究 → 一律手動，資料不離機
    }

    /// Reconcile this installation's saved and retired drafts without starting service work.
    func reloadDrafts(in store: LiteStore) async {
        recordStore = store
        guard let native else { return }
        let request = UUID()
        latestDraftRead = request
        guard store.confirmCaptureIndex() else {
            pendingCaptures = []
            draftNotice = "請先完整重讀紀錄，再恢復尚未存入的拍攝；原始檔案保留。"
            return
        }
        do {
            let saved = Set(store.records.map(\.id))
            let inventory = try await native.captures.inventory(saved: saved, retired: store.retiredCaptureIDs)
            var unresolved = inventory.unresolvedCount
            for id in inventory.needsFinalization {
                guard request == latestDraftRead else { return }
                do {
                    if let value = try await native.captures.unfinishedDraft(id) {
                        try await finalizeDraft(value, in: store)
                    }
                } catch { unresolved += 1 }
            }
            guard request == latestDraftRead else { return }
            guard store.confirmCaptureIndex() else { throw LiteServiceFailure.invalidState }
            let excluded = store.retiredCaptureIDs.union(store.records.map(\.id))
            pendingCaptures = inventory.pending.filter { !excluded.contains($0.id) }
            draftNotice = unresolved == 0 ? nil : "有 \(unresolved) 筆拍攝尚待核對。請解鎖後點「重新核對本機拍攝」。"
        } catch {
            if request == latestDraftRead {
                pendingCaptures = []
                draftNotice = store.needsReload ? store.storageError : "拍攝清單暫時無法核對。請解鎖後重讀；原檔保留。"
            }
        }
    }

    func retryDraftSave() async {
        guard let native, !busy, draft == nil, let jpeg = captureJPEG, let depth,
              let (id, date) = draftIdentity else { return }
        captureBusy = true; busyHint = "正在保存照片與深度…"; defer { captureBusy = false }
        do {
            draft = try await native.captures.create(jpeg: jpeg, depth: depth, id: id, now: date, researchPin: draftResearchPin)
            note = "照片與原始深度已保存在手機，尚未傳送。"
        } catch { note = "仍未完成本機保存，未送出照片。請保留此畫面。" }
    }

    var hasUnstoredCapture: Bool { image != nil && draft == nil && savedId == nil }

    @discardableResult
    func restoreDraft(_ id: String, in store: LiteStore, pending scope: LitePendingCaptureScope? = nil,
                      operation: String? = nil) async -> Bool {
        recordStore = store
        guard let native, !busy, store.captureCanResume(id), !store.records.contains(where: { $0.id == id }),
              scope == nil || !hasUnstoredCapture else { return false }
        captureBusy = true; busyHint = "正在讀回原拍攝…"; defer { captureBusy = false }
        do {
            let value = try await native.captures.load(id)
            guard value.savedRecordID == nil, let img = UIImage(data: value.jpeg) else { throw LiteServiceFailure.invalidState }
            let d = try value.depth()
            if let scope, (try? await native.identity.savedResearchContext()) != scope.context { return false }
            var journal: LiteServiceRecord?, unreadable = false
            do {
                journal = try await readRecognitionRecord(native, draft: value, width: d.rgbWidth, height: d.rgbHeight)
            }
            catch { unreadable = true }
            if let scope {
                guard let journal, journal.operation == operation, scope.contains(journal),
                      scope.isCurrent(in: native), !Task.isCancelled else { return false }
            }
            guard store.captureCanResume(id), !store.records.contains(where: { $0.id == id }) else { return false }
            gen += 1
            draftNeedsReload = false; resultApplicationPending = false
            image = img; captureJPEG = value.jpeg; depth = d
            imageW = d.rgbWidth; imageH = d.rgbHeight
            polys = value.polygons; source = value.source; contourOrigin = value.origin
            userEdited = value.manuallyEdited; draft = value; serviceResult = journal
            serviceUnreadable = unreadable; draftIdentity = (UUID(uuidString: value.id)!, value.createdAt)
            savedId = nil; cloudImageId = nil; cloudBinding = nil; cloudHadPolys = false
            estimate = nil; uploadMessage = nil
            note = unreadable ? "照片、深度與圈選已讀回；辨識回執無法核對，已停止雲端續傳。可繼續自行圈選並存入手機，原回執保留。"
                : "已讀回尚未存入紀錄的拍攝；照片、深度與圈選保留，未重新送出辨識。"
            if !polys.isEmpty { await runEstimate() }
            if !unreadable, journal?.note == "confirmed", !userEdited {
                do { try await useNativeResult() }
                catch { resultApplicationPending = true; note = Self.applySavedHint }
            }
            return true
        } catch {
            note = "無法完整核對這筆拍攝，未取代目前畫面或送出資料。原檔保留。"
            return false
        }
    }

    private static let applySavedHint = "原拍攝已讀回；辨識圈選尚未完整保存，可再次套用已保存的結果，不需重新辨識。"
    private static let serviceUnavailableHint = "個人服務暫時無法使用。可稍後再試，或在手機圈選。"
    private enum CaptureAccess {
        case account(LiteAccountIdentityStore.RecognitionAccess)
        case pendingRegistration, unreadableRegistration
    }

    private func recognitionAccess(_ session: LiteNativeSession) async -> CaptureAccess {
        guard !session.registering else { return .account(.checking) }
        await session.checkExistingRegistration()
        switch session.registration {
        case .required: return .account(.signedOut)
        case .confirmed: return .account(await session.identity.readRecognitionAccess())
        case .checking: return .account(.checking)
        case .pending: return .pendingRegistration
        case .unreadable: return .unreadableRegistration
        }
    }

    private func readRecognitionRecord(_ session: LiteNativeSession, draft: LiteCaptureDraft,
                                       width: Int, height: Int) async throws -> LiteServiceRecord? {
        let id = try LiteNativeSession.captureID(recordID: draft.id)
        let saved = try await session.service.storedIfPresent(id)
        if let saved {
            guard saved.imageSHA256 == LiteServiceBytes.hash(draft.jpeg),
                  saved.width == width, saved.height == height else { throw LiteServiceFailure.conflict }
        }
        return saved
    }

    var recognitionButtonTitle: String {
        switch serviceResult?.next {
        case "status": return "查詢這次辨識結果"
        case "run": return "送出原照片繼續辨識"
        case nil: return "使用個人雲端辨識"
        default: return "繼續辨識這張照片"
        }
    }

    /// Only the user's explicit resubmission can request a replacement operation.
    func recognizeNative(resubmit: Bool = false, fromButton: Bool = false) async {
        guard let native, let draft, !busy, manualEditor == nil, !serviceUnreadable, !draftNeedsReload else { return }
        guard recordStore?.captureCanResume(draft.id) != false else { note = "這次拍攝已捨棄或紀錄尚待核對，未再送出辨識。"; return }
        captureBusy = true; busyHint = "正在核對個人雲端辨識…"
        var statusOnly = false
        defer {
            if statusOnly { queryingRecognitionStatus = false }
            else { captureBusy = false }
        }
        let generation = gen
        let captureImage = image
        let queryRequested = serviceResult?.next == "status"
        func sameCapture() -> Bool {
            // Local outline edits can increment gen while a read for the same JPEG is in flight.
            self.draft?.id == draft.id && recordStore?.captureCanResume(draft.id) != false
                && (statusOnly ? image === captureImage : generation == gen)
        }
        guard (try? await native.captures.load(draft.id)) == draft else {
            if sameCapture() {
                draftNeedsReload = true
                note = "本機拍攝資料暫時無法核對。請重新讀取這次拍攝。"
            }
            return
        }
        do {
            let saved = try await readRecognitionRecord(native, draft: draft, width: imageW, height: imageH)
            guard sameCapture() else { return }
            guard saved != nil || serviceResult == nil else { throw LiteServiceFailure.conflict }
            serviceResult = saved
        } catch {
            if sameCapture() { serviceUnreadable = true; note = "辨識回執無法核對，暫停雲端處理；仍可保存本機圈選。" }
            return
        }
        if fromButton {
            let accountGeneration = native.identity.generation
            let access = await recognitionAccess(native)
            guard sameCapture(), accountGeneration == native.identity.generation else { return }
            switch access {
            case .account(.signedOut), .pendingRegistration:
                recognitionAwaitingTap = generation
                if case .pendingRegistration = access { note = Self.pendingRegistrationPrompt }
                else { note = Self.recognitionSignInPrompt }
                showRecognitionAccount = true
                return
            case .unreadableRegistration: note = Self.unreadableRegistrationPrompt; return
            case .account(.checking): note = "正在確認個人服務帳號，請稍候再點一次辨識。"; return
            case .account(.unavailable): note = Self.serviceUnavailableHint; return
            case .account(.available): recognitionAwaitingTap = nil
            }
        }
        do {
            let id = try LiteNativeSession.captureID(recordID: draft.id)
            let result: LiteServiceRecord
            if (queryRequested || serviceResult?.next == "status") && !resubmit {
                // Only this original-operation read can overlap hand drawing. Prepare/run stay exclusive.
                statusOnly = true
                queryingRecognitionStatus = true
                captureBusy = false
                result = try await native.service.resume(id, statusOnly: true) { _ in throw LiteServiceFailure.invalidState }
            } else {
                if resubmit { _ = try await native.service.requestResubmission(id) }
                result = try await native.recognizeSaved(recordID: draft.id, jpeg: draft.jpeg)
            }
            guard sameCapture() else { return }
            serviceResult = result
        } catch {
            do {
                let saved = try await readRecognitionRecord(native, draft: draft, width: imageW, height: imageH)
                guard sameCapture() else { return }
                guard saved != nil || serviceResult == nil else { throw LiteServiceFailure.conflict }
                serviceResult = saved
                if saved?.note != "confirmed" {
                    if let saved, saved.next == "none" { note = saved.message }
                    else { note = Self.serviceUnavailableHint }
                    return
                }
            } catch {
                if sameCapture() { serviceUnreadable = true; note = "辨識回執無法核對，暫停雲端處理；仍可保存本機圈選。" }
                return
            }
        }
        guard let result = serviceResult else { return }
        note = result.message
        if result.note == "confirmed", !userEdited {
            // Keep the durable receipt. A canvas already open owns its geometry, including unsaved edits.
            guard manualEditor == nil, !statusOnly || !captureBusy else {
                resultApplicationPending = true
                return
            }
            if statusOnly {
                captureBusy = true
                queryingRecognitionStatus = false
                statusOnly = false
            }
            do { try await useNativeResult() }
            catch { if sameCapture() { resultApplicationPending = true; note = Self.applySavedHint } }
        }
    }

    /// Both ways of closing the sheet return to the same draft without dispatching a photo.
    func recognitionAccountDismissed() async {
        guard !showRecognitionAccount, recognitionAwaitingTap == gen, !busy, let initialNote = note,
              initialNote == Self.recognitionSignInPrompt || initialNote == Self.pendingRegistrationPrompt,
              let draft, let native else { return }
        let captureGeneration = gen
        let accountGeneration = native.identity.generation
        let access = await recognitionAccess(native)
        guard gen == captureGeneration, native.identity.generation == accountGeneration,
              !showRecognitionAccount, !busy, self.draft?.id == draft.id, self.draft?.revision == draft.revision,
              recordStore?.captureCanResume(draft.id) != false, note == initialNote else { return }
        switch access {
        case .account(.available): note = "可用這張照片繼續雲端辨識，也可以直接在手機圈選。"
        case .account(.signedOut): note = "照片已保存在手機。可自行圈選，或登入後使用雲端辨識。"
        case .pendingRegistration: note = "裝置登記仍待續接。照片已存在手機，可先圈選，或點辨識返回個人服務。"
        case .unreadableRegistration: note = Self.unreadableRegistrationPrompt
        case .account(.unavailable): note = Self.serviceUnavailableHint
        case .account(.checking): note = "正在確認個人服務帳號，請稍候再點一次辨識。"
        }
    }

    /// Foreground continuation of an already requested operation only. Never
    /// creates a first job or a successor, and honors the durable retry deadline.
    func resumePendingRecognition() async {
        guard let native, !busy, manualEditor == nil, recognitionAwaitingTap != gen, let result = serviceResult, !serviceUnreadable,
              result.next == "status",
              result.retryAt <= native.identity.now().timeIntervalSince1970 else { return }
        await recognizeNative()
    }

    /// Explicit replacement is required after a manual edit. Service result hashes
    /// and geometry are checked before changing the draft or estimating an area.
    func useNativeResult(selecting index: Int? = nil) async throws {
        guard let native, let draft, let r = serviceResult, r.note == "confirmed",
              recordStore?.captureCanResume(draft.id) != false,
              r.captureID == (try LiteNativeSession.captureID(recordID: draft.id)),
              r.origin == native.origin.absoluteString, r.imageSHA256 == LiteServiceBytes.hash(draft.jpeg),
              r.width == imageW, r.height == imageH, let answer = r.reply?.result else { throw LiteServiceFailure.conflict }
        try r.validate()
        let candidates = answer.wound_polygons.map { $0.map { $0.map { Int($0.rounded(.down)) } } }
            .filter { DepthCapture.polygonsFit([$0], width: imageW, height: imageH) }
        var targets: LiteContourTargets?
        var chosen = candidates
        if candidates.count > 1 {
            guard let hash = r.reply?.receipt?.result_sha256 else { throw LiteServiceFailure.conflict }
            targets = LiteContourTargets(captureID: draft.id, revision: draft.revision, operation: r.operation,
                                         resultHash: hash, polygons: candidates, width: imageW, height: imageH)
            let saved = draft.source == "cloud" && !draft.manuallyEdited && draft.polygons.count == 1
                ? candidates.firstIndex(of: draft.polygons[0]) : nil
            guard let selected = index ?? saved else {
                verifiedTargets = targets
                resultApplicationPending = false
                note = "辨識到多個輪廓。請在照片中選擇這次要量測的一處。"
                return
            }
            guard candidates.indices.contains(selected) else { throw LiteServiceFailure.conflict }
            chosen = [candidates[selected]]
        }
        var next = draft
        next.polygons = chosen; next.source = "cloud"; next.manuallyEdited = false
        next.origin = .cloud(route: answer.route, contract: answer.student_output_contract)
        if next != draft {
            do { self.draft = try await native.captures.update(next, previous: draft) }
            catch {
                guard index != nil else { throw error }
                // A failed acknowledgement can still leave the exact chosen outline on disk.
                next.revision += 1
                guard let readback = try? await native.captures.load(draft.id), readback == next else {
                    draftNeedsReload = true
                    estimate = nil
                    throw error
                }
                self.draft = readback
            }
        }
        if let revision = self.draft?.revision { targets?.revision = revision }
        verifiedTargets = targets
        resultApplicationPending = false
        polys = chosen; source = next.source; contourOrigin = next.origin; userEdited = false
        note = chosen.isEmpty ? "未找到可用圈選；照片與深度已保留，可自行圈選。"
            : "已套用個人服務圈選。請確認邊界後存入紀錄；這次辨識不代表同意研究或已上傳研究資料。"
        await runEstimate()
    }

    func replaceWithNativeResult() async {
        guard !busy else { return }
        captureBusy = true; busyHint = "正在套用已保存的辨識結果…"; defer { captureBusy = false }
        do {
            try await useNativeResult()
            if let targets = availableTargets { presentedTargets = targets }
        }
        catch { note = "辨識結果與原拍攝無法核對，未取代圈選。" }
    }

    func confirmNativeTarget(_ index: Int, from offered: LiteContourTargets) async {
        guard !busy, offered == availableTargets, offered.polygons.indices.contains(index) else { return }
        captureBusy = true; busyHint = "正在套用已保存的辨識結果…"; defer { captureBusy = false }
        do {
            try await useNativeResult(selecting: index)
            presentedTargets = nil
        } catch {
            presentedTargets = nil
            note = "輪廓尚未套用。請重新讀取這次拍攝後再選擇。"
        }
    }

    func markDraftSaved(_ value: LiteCaptureDraft, in store: LiteStore) async {
        do { try await finalizeDraft(value, in: store) }
        catch { draftNotice = "量測已存入紀錄，拍攝草稿的結案標記尚待核對。原檔保留。" }
    }

    private func finalizeDraft(_ value: LiteCaptureDraft, in store: LiteStore) async throws {
        guard let native, store.confirmCaptureIndex(), let row = store.records.first(where: { $0.id == value.id }),
              store.images.rawBytes(row.imageName) == value.jpeg,
              let depthName = row.depthName,
              store.images.rawBytes(depthName) == value.encodedDepth else { throw LiteServiceFailure.conflict }
        // The saved record may already have a later mask revision. Exact RGB-D
        // identity is enough to retire the old capture copy, never to overwrite it.
        try await native.captures.finalize(value)
    }

    /// The UI and recovery tests use this same commit path. Photos/depth stay
    /// intact on an uncertain index write; only a verified save clears the UI.
    func saveCapture(in store: LiteStore, selectedWoundID: String,
                     location: LiteWoundLocation, permit: LiteWoundPermit? = nil) -> LiteRecord? {
        recordStore = store
        guard savedId == nil, !busy, !store.needsReload,
              !usesServiceDraft || draft != nil,
              let e = estimate, liteVerdict(e).usable, let jpeg = captureJPEG, let depth else { return nil }
        guard draft.map({ store.captureCanResume($0.id) }) != false else {
            note = "這次拍攝已捨棄或紀錄尚待核對，未重新存入。"; return nil
        }
        if let draft, store.records.contains(where: { $0.id == draft.id }) {
            note = "這次拍攝已存入紀錄，請從紀錄頁繼續；未建立重複資料。"
            return nil
        }
        guard let name = store.images.save(jpeg: jpeg),
              let depthName = store.saveDepth(depth, matchingJPEG: jpeg) else {
            note = "照片與深度尚未完整存入紀錄，請保留此畫面並重試存檔。"
            return nil
        }
        let v = liteVerdict(e)
        let polysJSON = (try? JSONEncoder().encode(polys)).flatMap { String(data: $0, encoding: .utf8) }
        var record = LiteRecord(id: draft?.id ?? UUID().uuidString,
            dateISO: ISO8601DateFormatter().string(from: draft?.createdAt ?? Date()),
            surfaceCm2: e.surfaceAreaCm2, projectedCm2: e.projectedAreaCm2,
            tiltDeg: e.tiltDeg, volumeMl: e.volumeMl, maxDepthMm: e.maxDepthMm,
            quality: v.warnings.isEmpty ? "ok" : "注意", imageName: name, source: source,
            polysJson: polysJSON, depthName: depthName)
        record.cloudSync = cloudBinding.map { LiteCloudSyncState(binding: $0) }
        record.measurementContext = LiteMeasurementContext(depth: depth, result: e, warnings: v.warnings)
        record.contourOrigin = contourOrigin; record.manuallyConfirmed = hasManualContour
        if let pin = draft?.researchPin, pin.scope == captureParticipation(), native?.researchDelivery?.permits(pin.scope) == true,
           let cameraID = depth.captureProvenance?.id.uuidString.lowercased(), !serviceUnreadable {
            let result = serviceResult
            let hash = LiteServiceBytes.hash(jpeg)
            let recognized = result?.note == "confirmed" && result?.reply?.result != nil
                && result?.reply?.receipt?.result_sha256 != nil && result?.imageSHA256 == hash
                && result?.installation == pin.account.installation && result?.origin == pin.account.origin
            if recognized || (result == nil && source == "manual" && hasManualContour) {
                record.researchCapture = LitePendingResearchCapture(pin: pin, captureID: cameraID,
                    imageHash: hash, operation: recognized ? result?.operation : nil,
                    resultHash: recognized ? result?.reply?.receipt?.result_sha256 : nil, width: imageW, height: imageH)
            }
        }
        guard let saved = store.saveMeasurement(record, selectedWoundID: selectedWoundID,
            enteredLocation: location, queueResearchSync: usesServiceDraft ? record.researchCapture != nil : LitePrefs.researchConsent == true,
            permit: permit) else {
            let message = store.storageError
            // A write may have committed before reporting an error. Read back
            // this exact newly allocated pair instead of deleting referenced data.
            if store.reload(), let committed = store.records.first(where: { $0.id == record.id }),
               committed.imageName == name, committed.depthName == depthName,
               committed.polysJson == polysJSON, committed.surfaceCm2 == e.surfaceAreaCm2,
               store.images.rawBytes(name) == jpeg,
               store.loadDepth(depthName, matchingImage: name) != nil {
                savedId = committed.id
                return committed
            }
            note = message ?? "紀錄尚未確認存入；照片與深度保留，未清空量測。"
            return nil
        }
        savedId = saved.id
        return saved
    }

    /// User-confirmed local discard. No service call, quota change, or research
    /// withdrawal is implied. A durable fence precedes removal of the raw copy.
    func discardDraft(_ id: String, in store: LiteStore) async {
        guard let native, !busy, !store.needsReload,
              !store.records.contains(where: { $0.id == id }) else { return }
        captureBusy = true; busyHint = "正在捨棄本機拍攝…"; defer { captureBusy = false }
        guard store.retireCapture(id, requireSavedRecord: false) else {
            note = store.storageError; return
        }
        do {
            try await native.captures.retire([id])
            if draft?.id == id { clearCapture() }
            await reloadDrafts(in: store)
            note = "已捨棄這次拍攝草稿；服務回執保留，未更動研究同意。"
        } catch {
            note = "捨棄要求已保存，但本機檔案尚未確認清理完成；已停止此筆續傳，請重試捨棄。"
        }
    }

    func beginManualEditing() {
        guard manualEditor == nil, canEditOutline, let image else { return }
        manualSaveProblem = nil
        manualEditor = ManualEditor(image: image, polygons: polys, generation: gen, draftID: draft?.id, revision: draft?.revision)
    }

    private func matchesManualEditor(_ editor: ManualEditor) -> Bool {
        manualEditor?.id == editor.id && gen == editor.generation && image === editor.image
            && draft?.id == editor.draftID && draft?.revision == editor.revision
            && (editor.draftID.map { recordStore?.captureCanResume($0) != false } ?? true)
    }

    /// Finish only after the edit has been committed to the same capture.
    @discardableResult
    func applyManual(_ traced: [[[Int]]], correctionIoU: Double? = nil, editor: ManualEditor? = nil) async -> Bool {
        func failed(_ text: String) -> Bool {
            if let editor {
                if manualEditor?.id == editor.id { manualSaveProblem = text }
            } else { note = text }
            return false
        }
        let changedCapture = "原草稿已變更。請取消圈選，再從原拍攝開啟。"
        if let editor, !matchesManualEditor(editor) { return failed(changedCapture) }
        guard !captureBusy else { return failed("正在處理這次拍攝。請稍候再按「完成圈選」。") }
        manualSaveProblem = nil
        captureBusy = true; busyHint = "正在保存圈選…"; defer { captureBusy = false }
        if correctionIoU == 1, !polys.isEmpty {
            if let editor, let native, let draft {
                guard let original = try? await native.captures.load(draft.id) else {
                    return failed("原草稿目前無法讀取。請解鎖手機後再按「完成圈選」。")
                }
                guard original == draft, matchesManualEditor(editor) else { return failed(changedCapture) }
            }
            return true
        }
        let picked = Self.centerWound(traced, w: imageW, h: imageH)
        if usesServiceDraft, let native {
            guard let draft else { return failed("請先完成照片與深度的本機保存，再修正圈選。") }
            var next = draft; next.polygons = picked; next.source = "manual"; next.manuallyEdited = true
            let saved: LiteCaptureDraft
            do { saved = try await native.captures.update(next, previous: draft) }
            catch {
                // A write may have committed even when its acknowledgement was lost.
                next.revision += 1
                let current = try? await native.captures.load(draft.id)
                if let current, current == next { saved = current }
                else if let current, current != draft { return failed(changedCapture) }
                else { return failed("圈選尚未保存。請保留畫布，確認手機儲存空間後再按「完成圈選」。") }
            }
            if let editor, !matchesManualEditor(editor) { return failed(changedCapture) }
            self.draft = saved
        }
        polys = picked
        source = "manual"
        userEdited = true
        if traced.count > 1, picked.count == 1 {
            note = "圈選含多個分離區塊，已取最靠近畫面中央的一塊。"
        }
        await runEstimate()
        return true
    }

    /// 單一中心傷口：取質心最靠近畫面中央的輪廓（民眾版產品決策 2026-08-18）。
    nonisolated static func centerWound(_ all: [[[Int]]], w: Int, h: Int) -> [[[Int]]] {
        let valid = all.filter { DepthCapture.polygonsFit([$0], width: w, height: h) }
        guard valid.count > 1 else { return valid }
        let cx = Double(w) / 2, cy = Double(h) / 2
        let best = valid.min { a, b in
            func d2(_ p: [[Int]]) -> Double {
                let mx = Double(p.reduce(0) { $0 + $1[0] }) / Double(p.count)
                let my = Double(p.reduce(0) { $0 + $1[1] }) / Double(p.count)
                return (mx - cx) * (mx - cx) + (my - cy) * (my - cy)
            }
            return d2(a) < d2(b)
        }
        return best.map { [$0] } ?? []
    }

    private func runEstimate() async {
        guard let d = depth, !polys.isEmpty, imageW > 0 else { estimate = nil; return }
        gen += 1
        let g = gen
        let alreadyBusy = captureBusy
        captureBusy = true
        busyHint = "深度幾何計算中…"
        defer { captureBusy = alreadyBusy }
        let p = polys, iw = imageW, ih = imageH
        let est = await Task.detached(priority: .userInitiated) {
            DepthAreaEstimator.estimate(polygons: p, depth: d, imageW: iw, imageH: ih)
        }.value
        if gen == g { estimate = est }
    }

    /// The segmentation service must reply in the exact submitted JPEG space.
    /// Do not silently resize/crop depth or replace its bound RGB dimensions.
    func acceptsCloudGeometry(_ result: BackendClient.LiteSegmentResult) -> Bool {
        result.imageW == imageW && result.imageH == imageH && imageW > 0 && imageH > 0
            && DepthCapture.polygonsFit(result.polygons, width: imageW, height: imageH)
    }

    func submitLayLabel(store: LiteStore) {
        guard let recordID = savedId else { return }
        uploadMessage = "本機已保存；正在核對同步狀態…"
        Task {
            await store.syncRevision(recordID: recordID)
            guard savedId == recordID else { return }
            uploadMessage = LitePrefs.researchConsent == true
                ? store.records.first(where: { $0.id == recordID })?.cloudSyncDescription
                : "僅更新此裝置；研究上傳未開啟。"
        }
    }

    enum LiteCloudOutcome {
        case ok([[[Int]]], stored: Bool, route: String?, contract: String?, raw: Int, receipt: LiteStorageReceipt?)
        case softFail(String)     // 429 配額／人臉退件：後端給的可讀訊息
        case hardFail
    }

    /**
     匿名辨識（`/api/v1/lite/segment`，2026-08-19 切換；免帳號免登入）。

     同意研究時附上同一次 JPEG 與原始 Float32 深度資料包。缺少方向等
     必要資訊時保留舊 PNG 路徑，但不宣稱原始深度或感測器信心值已保存。
     編碼是 MB 級工作，照例移出主執行緒。
     */
    private func liteCloudSegment(_ work: UIImage, depth d: DepthCapture?, researchScope: LiteCaptureResearchScope) async -> LiteCloudOutcome {
        guard let jpeg = captureJPEG else { return .hardFail }
        let payload: (jpeg: Data, dep: String?, conf: String?, k: [String: Double]?, raw: LiteRawDepthPacket?)? =
            await Task.detached(priority: .userInitiated) {
                if let d, let packet = try? LiteRawDepthPacket.make(depth: d, jpeg: jpeg) {
                    return (jpeg, nil, nil, nil, packet)
                }
                guard let d, let enc = DepthAreaEstimator.encodePng16mm(d) else {
                    return (jpeg, nil, nil, nil, nil)
                }
                return (jpeg,
                        enc.depthPng.base64EncodedString(),
                        enc.confPng.base64EncodedString(),
                        DepthAreaEstimator.intrinsicsForUpload(d), nil)
            }.value
        guard let payload else { return .hardFail }
        let server = researchScope.origin
        let c = BackendClient(baseUrl: server, liteTransport: LiteSignedCloudTransport(baseURL: server, scopeGuard: {
            await MainActor.run {
                researchScope.permits(server: AppSettings.backendURL(), consent: LitePrefs.researchConsent == true)
            }
        }))
        do {
            let anonId = try await c.liteInstallation()
            let researchEpoch = try LiteWithdrawalJournal.standard.epoch(server: server, owner: anonId)
            guard researchEpoch == researchScope.epoch else { return .softFail("研究同意已變更，此張保留在手機，請手動圈選。") }
            let r = try await c.liteSegment(jpeg: payload.jpeg,
                                            anonId: anonId,
                                            consentVersion: LitePrefs.consentVersion,
                                            depthMapPngBase64: payload.dep,
                                            depthConfPngBase64: payload.conf,
                                            cameraIntrinsics: payload.k, rawCapture: payload.raw,
                                            researchEpoch: researchEpoch)
            LiteUsageState.shared.record(r.quota, server: server, anonId: anonId, epoch: researchEpoch)
            if let m = r.userMessage { return .softFail(m) }
            guard acceptsCloudGeometry(r) else {
                return .softFail("辨識回傳的影像尺寸或輪廓與原照片不符，未套用結果。請在原照片手動圈選；這不代表先前送出的影像已撤回。")
            }
            cloudImageId = r.imageId   // 落地鍵（stored=false 時後端本來就回 nil）
            if r.stored, let imageID = r.imageId, !imageID.isEmpty,
               r.imageW == work.cgImage?.width, r.imageH == work.cgImage?.height {
                cloudBinding = LiteCloudBinding(server: server, anonID: anonId, imageID: imageID,
                    imageW: r.imageW > 0 ? r.imageW : imageW, imageH: r.imageH > 0 ? r.imageH : imageH,
                    storageReceipt: r.storageReceipt, researchEpoch: researchEpoch)
            }
            // Keep the original JPEG/depth coordinate binding unchanged.
            return .ok(r.polygons, stored: r.stored, route: r.route, contract: r.studentOutputContract, raw: r.rawPolyCount, receipt: r.storageReceipt)
        } catch {
            if let message = (error as? LiteAttestFailure)?.captureFailureMessage {
                return .softFail(message)
            }
            // Debug 組建把確切錯誤（HTTP 狀態＋伺服器訊息／逾時）掛上畫面——
            // 「連線或服務問題」六個字對排錯毫無鑑別力（2026-08-19 教訓：
            // 端點 500、404、逾時、DNS 全都長這樣）。
            #if DEBUG
            return .softFail("雲端辨識未成功，請手動圈選。\n🔧 \(String(describing: error))")
            #else
            return .hardFail
            #endif
        }
    }
}

// MARK: - 品質判定（民眾版比醫療版更嚴：不合格就不給數字）

struct LiteVerdict {
    var usable: Bool
    var blocker: String?      // usable=false 的原因（請重拍文案）
    var warnings: [String]    // usable=true 時的注意事項
}

func liteVerdict(_ e: DepthAreaResult) -> LiteVerdict {
    guard [e.surfaceAreaCm2, e.projectedAreaCm2, e.coverage, e.medianDistanceM].allSatisfy(\.isFinite),
          e.surfaceAreaCm2 > 0, e.projectedAreaCm2 > 0, (0...1).contains(e.coverage),
          e.medianDistanceM > 0, e.medianDistanceM < 5, // Upper bound of captured samples used by DepthAreaEstimator.
          e.tiltDeg.map({ $0.isFinite && (0...90).contains($0) }) ?? true else {
        return LiteVerdict(usable: false, blocker: "量測資料不完整，無法提供面積。請保留原紀錄並重新拍攝。", warnings: [])
    }
    let ratio = e.projectedAreaCm2 > 0 ? e.surfaceAreaCm2 / e.projectedAreaCm2 : 99
    if ratio > 1.25, (e.tiltDeg ?? 0) < 25 {
        return LiteVerdict(usable: false,
                           blocker: "深度品質不足（表面雜訊偏高）。請補光、避免反光與濕亮面，保持 25–40 公分重拍。",
                           warnings: [])
    }
    if e.medianDistanceM < 0.22 {
        return LiteVerdict(usable: false,
                           blocker: "拍攝距離太近，低於目前使用的距離檢查範圍。請退後至約 25–40 公分重拍。",
                           warnings: [])
    }
    if e.coverage < 0.5 {
        return LiteVerdict(usable: false,
                           blocker: "傷口區域的深度資料太少（可能反光或濕潤）。請調整光線與角度重拍。",
                           warnings: [])
    }
    var warns: [String] = []
    if let t = e.tiltDeg, t > 25 {
        warns.append("拍攝角度偏斜（估計約 \(Int(t))°），辨識邊界與面積誤差可能增加。結果僅供參考，建議正對傷口重拍。")
    } else if let t = e.tiltDeg, t > 10 {
        warns.append("拍攝略斜（估計約 \(Int(t))°）。建議正對傷口拍攝，並確認圈選邊界。")
    }
    if e.medianDistanceM > 0.5 {
        warns.append("距離偏遠（約 \(Int(e.medianDistanceM * 100)) 公分），建議 25–40 公分。")
    }
    if e.coverage < 1 {
        warns.append("部分區域缺少深度，面積含補值估算；缺漏可能造成偏差，無法保證誤差大小。")
    }
    if e.tiltDeg == nil { warns.append("無法估計周邊參考面與拍攝傾角；請正對傷口，勿把未知傾角視為零度。") }
    if e.surfaceAreaCm2 < 1.0 {
        warns.append("傷口小於 1 cm²，相對誤差較大，數字僅供趨勢參考。")
    }
    return LiteVerdict(usable: true, blocker: nil, warnings: warns)
}

// MARK: - 量測頁

struct LiteMeasureView: View {
    @Environment(\.locale) private var locale
    private var copy: LiteCaptureCopy { LiteCaptureCopy(locale: locale) }
    @ObservedObject var store: LiteStore
    var onSaved: (String) -> Void = { _ in }
    @StateObject private var vm: LiteMeasureVM
    let nativeSession: LiteNativeSession?
    init(store: LiteStore, nativeSession: LiteNativeSession? = nil, onSaved: @escaping (String) -> Void = { _ in }) {
        self.store = store; self.nativeSession = nativeSession; self.onSaved = onSaved
        _vm = StateObject(wrappedValue: LiteMeasureVM(native: nativeSession))
    }
    @Environment(\.scenePhase) private var scenePhase
    @State private var confirmServiceReplacement = false
    @State private var discardCaptureID: String?
    @State private var elapsedQuotaRetryAt: TimeInterval?
    @State private var showCamera = false
    @State private var savingTrace = false
    @State private var woundID = ""
    @State private var woundSide = ""
    @State private var woundSite = ""
    @State private var addingNewWound = false
    @State private var saveTarget: LiteWoundSaveTarget?
    @State private var showSaveConfirmation = false
    @State private var saveNotice: String?
    @State private var confirmRetake = false
    @State private var checkingWound = false
    @State private var roster: LiteWoundRoster?

    private var availableGroups: [LiteWoundGroup] {
        let local = store.wounds
        return roster?.selectableGroups(in: local) ?? local.filter { !$0.ended }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        if vm.image != nil, vm.savedId == nil { confirmRetake = true }
                        else { showCamera = true }
                    } label: {
                        Label(copy.text(vm.captureButtonTitle), systemImage: "camera.fill")
                            .frame(maxWidth: .infinity).padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.busy)
                    .accessibilityIdentifier("lite.capture")

                    Text(copy.captureHint(inCamera: false))
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("lite.capture.instructions")

                    if let nativeSession {
                        LiteMeasureAllowanceView(session: nativeSession, measure: vm, store: store)
                        if !vm.pendingCaptures.isEmpty {
                            Menu(copy.format("繼續尚未存入的拍攝（%@）", String(vm.pendingCaptures.count))) {
                                ForEach(vm.pendingCaptures) { item in
                                    Menu(item.createdAt.formatted(date: .numeric, time: .shortened)) {
                                        Button(copy.text("繼續圈選或辨識")) { Task { await vm.restoreDraft(item.id, in: store) } }
                                        Button(copy.text("捨棄這次拍攝"), role: .destructive) { discardCaptureID = item.id }
                                    }
                                }
                            }.disabled(vm.busy || store.needsReload)
                        }
                        if let notice = vm.draftNotice {
                            Text(copy.message(notice)).font(.footnote).foregroundStyle(.orange)
                            Button(copy.text("重新核對本機拍攝")) { Task { await vm.reloadDrafts(in: store) } }.disabled(vm.busy)
                        }
                    } else if !LiteNativeBuild.includesService, LitePrefs.researchConsent == true {
                        LiteResearchQuotaView()
                    }
                    if vm.image == nil || !vm.usesServiceDraft { measurementFeedback }

                    if let img = vm.image {
                        LitePreview(image: img, polys: vm.polys.isEmpty ? vm.availableTargets?.polygons ?? [] : vm.polys,
                                    imageW: vm.imageW, imageH: vm.imageH)
                        if vm.usesServiceDraft {
                            measurementFeedback
                            nativeRecognition
                        }
                        if vm.depth != nil {
                            Button(copy.text(vm.polys.isEmpty ? "圈選傷口" : "修正圈選")) { vm.beginManualEditing() }
                                .buttonStyle(.bordered)
                                .disabled(!vm.canEditOutline)
                        }
                    }

                    if let e = vm.estimate {
                        if let notice = vm.contourOrigin.uncertaintyNotice {
                            Text(copy.message(notice)).font(.footnote).foregroundStyle(.orange)
                        }
                        LiteResultCard(e: e, verdict: liteVerdict(e))
                        saveRow(e: e)
                    }
                    if let draft = vm.draft {
                        Button(copy.text("捨棄這次拍攝"), role: .destructive) { discardCaptureID = draft.id }
                            .disabled(vm.busy)
                    }
                }
                .padding()
            }
            .disabled(checkingWound)
            .navigationTitle(copy.text("量測"))
            .alert(copy.text("重新拍攝？"), isPresented: $confirmRetake) {
                Button(copy.text("保留目前量測"), role: .cancel) { }
                Button(copy.text("重新拍攝")) { showCamera = true }
            } message: {
                Text(copy.text("目前量測尚未存入紀錄。拍下新照片後會取代這個畫面；已存入的紀錄不受影響。取消相機可回到目前量測。"))
            }
            .onAppear { if vm.image == nil { selectLastWound() } }
            .sheet(item: $vm.presentedTargets) { targets in
                if let image = vm.image, targets == vm.availableTargets {
                    LiteContourTargetPicker(image: image, targets: targets,
                                            original: vm.hasManualContour ? [] : vm.polys,
                                            busy: vm.busy) { index in
                        await vm.confirmNativeTarget(index, from: targets)
                    }
                }
            }
            .task(id: nativeSession.map(ObjectIdentifier.init)) {
                vm.attachNativeSession(nativeSession)
                await vm.reloadDrafts(in: store)
            }
            .sheet(isPresented: $vm.showRecognitionAccount, onDismiss: {
                Task { await vm.recognitionAccountDismissed() }
            }) {
                if let nativeSession {
                    NavigationStack {
                        LiteNativeSessionView(session: nativeSession)
                            .toolbar {
                                ToolbarItem(placement: .confirmationAction) {
                                    Button(copy.text("返回這次拍攝")) { vm.showRecognitionAccount = false }
                                        .accessibilityIdentifier("lite.capture.returnFromAccount")
                                }
                            }
                    }
                }
            }
            .confirmationDialog(copy.text("捨棄尚未存入的拍攝？"), isPresented: Binding(
                get: { discardCaptureID != nil }, set: { if !$0 { discardCaptureID = nil } }), titleVisibility: .visible) {
                Button(copy.text("捨棄草稿照片與深度"), role: .destructive) {
                    if let id = discardCaptureID { Task { await vm.discardDraft(id, in: store) } }
                    discardCaptureID = nil
                }
                Button(copy.text("保留"), role: .cancel) { discardCaptureID = nil }
            } message: {
                Text(copy.text("此操作無法復原，不退還已使用的辨識額度，也不會撤回已傳送的研究資料。服務回執仍保留供核對。"))
            }
            .task(id: nativeSession != nil && scenePhase == .active) {
                guard nativeSession != nil, scenePhase == .active else { return }
                while !Task.isCancelled {
                    do { try await Task.sleep(nanoseconds: 5_000_000_000) } catch { return }
                    await vm.resumePendingRecognition()
                }
            }
            .alert(copy.text("以辨識結果取代目前圈選？"), isPresented: $confirmServiceReplacement) {
                Button(copy.text("保留目前圈選"), role: .cancel) { }
                Button(copy.text("套用已保存的辨識結果")) { Task { await vm.replaceWithNativeResult() } }
            } message: {
                Text(copy.text("這會取代這次拍攝尚未存入紀錄的圈選，不會重新辨識或使用額度。"))
            }
            .alert(copy.text("確認傷口位置"), isPresented: $showSaveConfirmation, presenting: saveTarget) { target in
                Button(copy.text("返回修改"), role: .cancel) { saveTarget = nil }
                Button(copy.text("確認並存入")) { saveConfirmed(target) }
            } message: { target in
                Text(copy.confirmation(target))
            }
            .fullScreenCover(isPresented: $showCamera) {
                CameraCaptureView(
                    // 「只拍傷口」不只是構圖建議，是去識別化的一環：協定層去得掉 EXIF 與
                    // 檔名身分，去不掉畫面裡的臉、證件與環境——那只能靠拍攝當下不要入鏡。
                    hintText: copy.captureHint(inCamera: true),
                    displayText: LiteCaptureControlsCopy(locale: locale).camera,
                    onCapture: { img, dep in
                        showCamera = false
                        selectLastWound()
                        Task {
                            vm.attachNativeSession(nativeSession)
                            let done = await vm.ingest(img, depth: dep)
                            if !done { vm.beginManualEditing() }
                            await vm.reloadDrafts(in: store)
                        }
                    },
                    onCancel: { showCamera = false })
            }
            .fullScreenCover(item: $vm.manualEditor) { editor in
                WoundEditView(image: editor.image, initialPolygons: editor.polygons,
                              originalArea: nil, tissueFrac: [:], exudate: nil,
                              mmPerPx: nil, resume: nil, wbGains: nil, boundaryOnly: true,
                              displayText: LiteCaptureControlsCopy(locale: locale).text,
                              onCancel: { if !savingTrace { vm.manualEditor = nil } },
                              onDone: { _, all, iou, _, _, _ in
                                  guard !savingTrace else { return }
                                  savingTrace = true
                                  Task { @MainActor in
                                      defer { savingTrace = false }
                                      let saved = await vm.applyManual(all, correctionIoU: iou, editor: editor)
                                      if saved, vm.manualEditor?.id == editor.id { vm.manualEditor = nil }
                                  }
                              })
                    .disabled(savingTrace)
                    .interactiveDismissDisabled(savingTrace)
                    .overlay(alignment: .top) {
                        if let issue = vm.manualSaveProblem {
                            Text(copy.message(issue)).font(.footnote)
                                .padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                                .accessibilityIdentifier("lite.capture.manualSaveProblem")
                                .allowsHitTesting(false)
                        }
                    }
            }
        }
    }

    @ViewBuilder
    private var measurementFeedback: some View {
        if vm.busy {
            HStack(spacing: 8) { ProgressView(); Text(copy.message(vm.activityHint)).font(.footnote) }
        }
        if let n = vm.note {
            Text(copy.message(n)).font(.footnote)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.15)).cornerRadius(8)
        }
    }

    @ViewBuilder
    private var nativeRecognition: some View {
        if vm.draft == nil, vm.depth != nil {
            Button(copy.text("重試保存這次拍攝")) { Task { await vm.retryDraftSave(); await vm.reloadDrafts(in: store) } }
                .disabled(vm.busy)
        } else if vm.draft != nil {
            Text(copy.text("請只送出紙上標靶或人造模型的照片。個人雲端辨識會傳送這張照片並核對您的額度。您也可以在手機上自行圈選。研究上傳須另行同意。"))
                .font(.footnote).foregroundStyle(.secondary)
            if vm.draftNeedsReload || vm.serviceUnreadable {
                Button(copy.text("重新讀取這次拍攝")) {
                    if let id = vm.draft?.id { Task { await vm.restoreDraft(id, in: store) } }
                }.disabled(vm.busy)
            } else if vm.serviceResult?.note == "confirmed" {
                if let targets = vm.availableTargets, !vm.hasManualContour {
                    Button(copy.format("選擇本次量測的輪廓（%@ 處）", String(targets.polygons.count))) {
                        vm.presentedTargets = targets
                    }.buttonStyle(.borderedProminent).disabled(vm.busy)
                        .accessibilityIdentifier("lite.capture.chooseTarget")
                } else if vm.resultApplicationPending || vm.hasManualContour || vm.polys.isEmpty {
                    Button(copy.text("套用已保存的辨識圈選")) { confirmServiceReplacement = true }
                        .disabled(vm.busy)
                }
            } else if vm.serviceResult?.canResubmit == true {
                Button(copy.text("預留已逾時：重新送出")) { Task { await vm.recognizeNative(resubmit: true, fromButton: true) } }
                    .disabled(vm.busy)
            } else if let record = vm.serviceResult, record.next == "status" || record.note == "quota" {
                let retryDate = Date(timeIntervalSince1970: record.retryAt)
                let wait = retryDate.timeIntervalSinceNow
                let waiting = wait > 0 && wait <= 3600 && elapsedQuotaRetryAt != record.retryAt
                Button(waiting
                       ? copy.format("%@ 後可再試", retryDate.formatted(Date.FormatStyle(date: .omitted, time: .standard).locale(locale)))
                       : copy.text(vm.recognitionButtonTitle)) {
                    Task { await vm.recognizeNative(fromButton: true) }
                }.buttonStyle(.borderedProminent).disabled(vm.busy || waiting)
                    .accessibilityIdentifier("lite.capture.personalRecognition")
                    .task(id: record.retryAt) {
                        guard waiting else { return }
                        do { try await Task.sleep(for: .seconds(wait)) } catch { return }
                        elapsedQuotaRetryAt = record.retryAt
                    }
            } else if vm.serviceResult?.next != "none" {
                Button(copy.text(vm.recognitionButtonTitle)) {
                    Task { await vm.recognizeNative(fromButton: true) }
                }.buttonStyle(.borderedProminent).disabled(vm.busy)
                    .accessibilityIdentifier("lite.capture.personalRecognition")
            }
        }
    }

    @ViewBuilder
    private func saveRow(e: DepthAreaResult) -> some View {
        let v = liteVerdict(e)
        if v.usable {
            let selected = availableGroups.first { $0.id == woundID }
            let target = currentSaveTarget
            HStack(alignment: .top, spacing: 10) {
                Menu {
                    ForEach(availableGroups) { wound in
                        Button {
                            woundID = wound.id
                            addingNewWound = false
                            woundSide = ""; woundSite = ""
                            saveNotice = nil
                        } label: {
                            if !addingNewWound && woundID == wound.id {
                                Label(copy.history.trackingLabel(wound), systemImage: "checkmark")
                            } else {
                                Text(copy.history.trackingLabel(wound))
                            }
                        }
                    }
                } label: {
                    HStack {
                        Text(addingNewWound ? copy.text("選擇已紀錄傷口") : (selected?.name ?? copy.text("選擇已紀錄傷口")))
                            .multilineTextAlignment(.leading)
                        Image(systemName: "chevron.down")
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .padding(.horizontal, 10)
                    .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                }
                .disabled(availableGroups.isEmpty)
                .accessibilityLabel(copy.text("選擇已紀錄傷口"))
                Button {
                    addingNewWound = true
                    woundSide = ""; woundSite = ""
                    saveNotice = nil
                } label: {
                    Text(copy.text("新增紀錄傷口＋"))
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .padding(.horizontal, 10)
                        .foregroundStyle(addingNewWound ? Color.white : Color.accentColor)
                        .background(addingNewWound ? Color.accentColor : Color.secondary.opacity(0.12),
                                    in: RoundedRectangle(cornerRadius: 10))
                }
                .accessibilityAddTraits(addingNewWound ? .isSelected : [])
            }
            .buttonStyle(.plain)
            .disabled(vm.savedId != nil || vm.busy)
            if !addingNewWound, let existingLocation = selected?.location {
                Label(copy.history.location(existingLocation), systemImage: "mappin.and.ellipse")
                    .font(.subheadline)
                Text(copy.text("沿用此傷口的位置，不需重填。"))
                    .font(.caption).foregroundStyle(.secondary)
            } else if addingNewWound || selected != nil {
                LiteLocationFields(side: $woundSide, site: $woundSite)
                    .disabled(vm.savedId != nil || vm.busy)
                Text(copy.text(addingNewWound ? "側別與詳細部位皆為必填。" : "此傷口的位置尚未齊全；補填會套用到同組舊紀錄。"))
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text(copy.text("請選擇已紀錄傷口，或點右側「新增紀錄傷口＋」。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let nativeSession {
                Button(copy.text("核對帳戶正在追蹤的傷口")) {
                    checkingWound = true
                    Task { @MainActor in
                        defer { checkingWound = false }
                        do {
                            roster = try await store.refreshTracking(from: nativeSession)
                            saveNotice = roster?.summary
                                ?? "目前使用本機免費追蹤。"
                        } catch { roster = nil; saveNotice = LiteWoundTrackingError.unavailable.localizedDescription }
                    }
                }
            }
            Text(copy.text("追蹤同一處傷口時，請選擇原有傷口；不同傷口的紀錄會分開顯示。"))
                .font(.caption).foregroundStyle(.secondary)
            if let error = store.storageError { Text(copy.message(error)).font(.footnote).foregroundStyle(.orange) }
            if let saveNotice { Text(roster?.summary == saveNotice ? copy.history.roster(roster) : copy.message(saveNotice)).font(.footnote).foregroundStyle(.orange) }
            Button(copy.text(vm.savedId == nil ? "存入紀錄" : "✓ 已存入紀錄")) {
                guard let target = currentSaveTarget else { return }
                saveTarget = target
                showSaveConfirmation = true
            }
            .buttonStyle(.bordered)
            .disabled(vm.savedId != nil || vm.busy || target == nil || store.needsReload)
            if let message = vm.uploadMessage {
                Text(copy.message(message)).font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    private var currentSaveTarget: LiteWoundSaveTarget? {
        LiteWoundSaveTarget.resolve(addingNew: addingNewWound, selectedID: woundID,
            entered: LiteWoundLocation(side: woundSide, site: woundSite), groups: availableGroups)
    }

    private func selectLastWound() {
        woundID = LiteGrouping.mostRecentWoundID(store.records.filter { $0.woundEnded != true }) ?? ""
        addingNewWound = false
        woundSide = ""; woundSite = ""
        saveTarget = nil; saveNotice = nil
    }

    private func saveConfirmed(_ target: LiteWoundSaveTarget) {
        defer { saveTarget = nil }
        guard vm.savedId == nil, !vm.busy, !store.needsReload,
              let e = vm.estimate, liteVerdict(e).usable else { return }
        guard currentSaveTarget == target else {
            saveNotice = "傷口或位置已變更，請重新確認後存入。"
            return
        }
        guard !checkingWound else { return }
        checkingWound = true
        let draftID = vm.draft?.id
        let image = vm.image
        let accountGeneration = nativeSession?.identity.generation
        let owner = availableGroups.first(where: { $0.id == target.woundID })?.account
        Task { @MainActor in
            defer { checkingWound = false }
            do {
                let id = target.woundID.isEmpty
                    ? try LiteWoundRoster.newID(for: draftID ?? UUID().uuidString) : target.woundID
                let permit = try await nativeSession?.woundPermit(id: id, owner: owner)
                guard nativeSession?.identity.generation == accountGeneration, vm.image === image,
                      vm.draft?.id == draftID, currentSaveTarget == target,
                      !store.needsReload, !vm.busy, vm.savedId == nil else { throw LiteWoundTrackingError.changed }
                guard store.maySave(to: id, permit: permit) else {
                    if permit == nil { throw LiteWoundTrackingError.full(1) }
                    throw LiteWoundTrackingError.changed
                }
                persistConfirmed(target, permit: permit)
            } catch {
                saveNotice = ((error as? LiteWoundTrackingError) ?? .unavailable).localizedDescription
            }
        }
    }

    private func persistConfirmed(_ target: LiteWoundSaveTarget, permit: LiteWoundPermit?) {
        guard let saved = vm.saveCapture(in: store, selectedWoundID: target.woundID,
                                        location: target.location, permit: permit) else { return }
        woundID = saved.woundID ?? ""
        addingNewWound = false
        vm.savedId = saved.id
        // Only the server receipt confirms annotation delivery; local saving stays independent.
        vm.submitLayLabel(store: store)
        // The durable measurement is authoritative even if draft finalization
        // is interrupted. Recovery excludes IDs already present in the index.
        let committedDraft = vm.draft
        if let id = vm.finishSavedCapture(in: store) {
            saveNotice = nil
            onSaved(id)
        }
        Task { @MainActor in
            if let committedDraft { await vm.markDraftSaved(committedDraft, in: store) }
            await vm.reloadDrafts(in: store)
        }
    }
}

// MARK: - 影像＋輪廓預覽

struct LitePreview: View {
    @Environment(\.locale) private var locale
    let image: UIImage
    let polys: [[[Int]]]
    let imageW: Int
    let imageH: Int

    var body: some View {
        let copy = LiteHistoryCopy(locale: locale)
        WoundImagePreview(image: image, polygons: polys, imageW: imageW, imageH: imageH, height: 280,
                          gestureHint: copy.text("雙指縮放與移動，檢查圈選邊界。"), resetTitle: copy.text("重設檢視"))
    }
}

/// A local view of one verified service reply. Indices refer to that reply only.
struct LiteContourTargets: Identifiable, Equatable {
    let captureID: String
    var revision: Int
    let operation: String
    let resultHash: String
    let polygons: [[[Int]]]
    let width: Int, height: Int
    var id: String { captureID + "/" + operation + "/" + resultHash + "/" + String(revision) }

    func relativeCenter(_ index: Int) -> CGPoint {
        let points = polygons[index]
        let count = CGFloat(points.count)
        return CGPoint(x: CGFloat(points.reduce(0) { $0 + $1[0] }) / count / CGFloat(width),
                       y: CGFloat(points.reduce(0) { $0 + $1[1] }) / count / CGFloat(height))
    }
}

private struct LiteContourTargetPicker: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.locale) private var locale
    private var copy: LiteCaptureCopy { LiteCaptureCopy(locale: locale) }
    let image: UIImage
    let targets: LiteContourTargets
    let busy: Bool
    let apply: (Int) async -> Void
    @State private var selected: Int?
    init(image: UIImage, targets: LiteContourTargets, original: [[[Int]]], busy: Bool,
         apply: @escaping (Int) async -> Void) {
        self.image = image; self.targets = targets; self.busy = busy; self.apply = apply
        _selected = State(initialValue: original.count == 1 ? targets.polygons.firstIndex(of: original[0]) : nil)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(copy.text("點照片上的編號或下方選項，查看輪廓後再確認。切換目標不另計辨識次數。"))
                    Image(uiImage: image).resizable().aspectRatio(contentMode: .fit)
                        .overlay {
                            GeometryReader { geometry in
                                let size = geometry.size
                                ZStack(alignment: .topLeading) {
                                    Canvas { context, _ in
                                        for i in targets.polygons.indices {
                                            let points = targets.polygons[i].map {
                                                CGPoint(x: CGFloat($0[0]) / CGFloat(targets.width) * size.width,
                                                        y: CGFloat($0[1]) / CGFloat(targets.height) * size.height)
                                            }
                                            var path = Path()
                                            path.addLines(points); path.closeSubpath()
                                            let active = selected == i
                                            let style = StrokeStyle(lineWidth: active ? 3 : 2,
                                                                    dash: active ? [] : [5, 3])
                                            context.stroke(path, with: .color(.black), lineWidth: active ? 5 : 3)
                                            context.stroke(path, with: .color(active ? .cyan : .white), style: style)
                                        }
                                    }
                                    ForEach(targets.polygons.indices, id: \.self) { i in
                                        let point = targets.relativeCenter(i)
                                        Button { selected = i } label: {
                                            Text(String(i + 1)).font(.footnote.bold().monospacedDigit())
                                                .foregroundStyle(selected == i ? .black : .white)
                                                .frame(minWidth: 24, minHeight: 24)
                                                .background(selected == i ? Color.cyan : .black, in: Circle())
                                                .overlay(Circle().stroke(.white))
                                                .padding(8)
                                        }.buttonStyle(.plain)
                                            .position(x: min(max(12, point.x * size.width), max(12, size.width - 12)),
                                                      y: min(max(12, point.y * size.height), max(12, size.height - 12)))
                                            .accessibilityHidden(true)
                                    }
                                }
                            }
                        }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 130))], alignment: .leading) {
                        ForEach(targets.polygons.indices, id: \.self) { i in
                            let point = targets.relativeCenter(i)
                            let vertical = point.y < 1/3 ? "上方" : point.y > 2/3 ? "下方" : "中間"
                            let horizontal = point.x < 1/3 ? "左側" : point.x > 2/3 ? "右側" : "中央"
                            Button {
                                selected = i
                            } label: {
                                Label(copy.format("輪廓 %@（%@、%@）", String(i + 1), copy.text(vertical), copy.text(horizontal)),
                                      systemImage: selected == i ? "checkmark.circle.fill" : "circle")
                            }.buttonStyle(.bordered)
                                .accessibilityAddTraits(selected == i ? .isSelected : [])
                                .accessibilityIdentifier("lite.capture.target.\(i + 1)")
                        }
                    }
                    Button(copy.text("確認量測所選輪廓")) {
                        if let selected { Task { await apply(selected) } }
                    }.buttonStyle(.borderedProminent).disabled(selected == nil || busy)
                        .accessibilityIdentifier("lite.capture.confirmTarget")
                }.padding()
            }
            .disabled(busy)
            .navigationTitle(copy.text("選擇量測目標"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(copy.text("取消")) { dismiss() }.disabled(busy)
                }
            }
            .interactiveDismissDisabled(busy)
        }
    }
}

// MARK: - 結果卡（表面積為主）

struct LiteResultCard: View {
    @Environment(\.locale) private var locale
    private var copy: LiteCaptureCopy { LiteCaptureCopy(locale: locale) }
    let e: DepthAreaResult
    let verdict: LiteVerdict

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if verdict.usable {
                Text(copy.format("LiDAR 表面積估算：%@ cm²", copy.history.decimal(e.surfaceAreaCm2)))
                    .font(.title3).bold()
                    .fixedSize(horizontal: false, vertical: true)
                Text(copy.text("（LiDAR 輔助表面積估算，僅供個人紀錄參考）"))
                    .font(.caption).foregroundStyle(.secondary)
                LiteVolumeReferenceView(volume: e.volumeMl, depth: e.maxDepthMm)
                Text(copy.projection(e))
                    .font(.caption2).foregroundStyle(.secondary)
                Text(copy.text("免貼紙深度估算；投影面積與表面積不同。深度覆蓋完整也不代表小凹凸已量準。"))
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(verdict.warnings, id: \.self) { w in
                    Text("⚠ " + copy.message(w)).font(.footnote).foregroundStyle(.orange)
                }
            } else {
                Text(copy.text("這次拍攝品質不足，未計算面積")).font(.headline)
                if let b = verdict.blocker {
                    Text(copy.message(b)).font(.footnote).foregroundStyle(.orange)
                }
            }
            Text(copy.text("健康參考工具，非醫療診斷。傷口惡化、發燒或大量滲液請就醫。"))
                .font(.caption2).foregroundStyle(.secondary)
            NavigationLink(copy.text("精確度與限制")) { LiteMeasurementInfoView() }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((verdict.usable ? Color.blue : Color.orange).opacity(0.08))
        .cornerRadius(10)
    }
}
