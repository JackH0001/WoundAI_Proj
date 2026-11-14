import XCTest
@testable import WoundLite

@MainActor final class LiteResearchCopyTests: XCTestCase {
    private let en = LiteResearchCopy(locale: Locale(identifier: "en_US"))
    private let zh = LiteResearchCopy(locale: Locale(identifier: "zh_Hant_TW"))

    private func table(_ language: String) throws -> [String: String] {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteResearch", withExtension: "strings", subdirectory: nil, localization: language))
        return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String: String])
    }

    func testPackagedTablesPreserveChineseAndFormatArguments() throws {
        let english = try table("en"), chinese = try table("zh-Hant")
        XCTAssertEqual(Set(english.keys), Set(chinese.keys))
        XCTAssertGreaterThan(english.count, 130)
        for (key, value) in english {
            XCTAssertEqual(chinese[key], key)
            XCTAssertFalse(value.isEmpty)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, key)
            XCTAssertEqual(key.components(separatedBy: "%@").count, value.components(separatedBy: "%@").count, key)
        }
    }

    func testResearchDisclosureIncludesWholeDepthAndLinkableRecords() {
        let text = en.text("若拍到 LiDAR 深度，會一併傳送整張深度圖和相機參數。圈選外的物品也可能留下距離資訊。請避開臉部、姓名與證件。隨機安裝代碼可連結同一安裝的歷次資料。")
        for word in ["entire depth map", "camera parameters", "outside the outline", "link records", "over time"] {
            XCTAssertTrue(text.contains(word), word)
        }
        let outlines = en.text("研究資料包括照片、量測估算、傷口分組與側別部位。若有 AI 圈選，即使您沒有修邊，也可保存原始輪廓。您確認或修改的輪廓另有紀錄。")
        for word in ["measurement estimates", "body side and site", "Original AI outlines", "recorded separately"] {
            XCTAssertTrue(outlines.contains(word), word)
        }
    }

    func testPersonalServiceAndResearchChoicesRemainDistinct() {
        XCTAssertEqual(en.text("不同意研究上傳，繼續使用"), "Continue without research uploads")
        XCTAssertEqual(en.text("不同意，完全離線手動圈選"), "Do not agree; outline manually offline")
        let personal = en.text("這項選擇只決定研究上傳。個人雲端辨識另行登入，由您決定是否送出照片。")
        XCTAssertTrue(personal.contains("research uploads only")); XCTAssertTrue(personal.contains("separate sign-in"))
        let quota = en.text("免費追蹤 1 處，Plus 每帳戶最多同時追蹤 5 處。每日個人辨識總額為免費 2 次、Plus 10 次。本機圈選、保存及分享時間軸 PDF 不扣辨識次數。")
        for word in ["1 wound", "5", "2", "10", "do not use"] { XCTAssertTrue(quota.contains(word), word) }
    }

    func testRenewalDoesNotPromiseRestorationOrRetroactiveUploads() {
        let text = en.text("先前撤回已完成，已清除的雲端影像與深度不會恢復。這次只重新同意之後新拍攝的資料上傳；舊紀錄、舊待傳及未上傳的本機照片都不會自動補送。沿用原裝置身分，並保留撤回與本次同意的稽核紀錄。")
        for word in ["not be restored", "new captures made afterwards", "not be sent automatically", "audit"] {
            XCTAssertTrue(text.contains(word), word)
        }
        let pending = en.text("必須取得雲端重新加入的確認，才會開啟研究上傳；中斷時保留同一次要求供核對。")
        XCTAssertTrue(pending.contains("stay off")); XCTAssertTrue(pending.contains("same request"))
    }

    func testVisibleMediaRemovalDoesNotClaimCompleteDeletion() {
        let title = en.text("已確認清除目前可見媒體")
        XCTAssertTrue(title.lowercased().contains("currently visible"))
        let limit = en.text("歷史版本、備份、既有研究標註及已訓練模型的處理尚未完成驗證，不能保證全部刪除。撤回後本安裝維持離線，不會自動建立新身分或恢復研究上傳。")
        for word in ["backups", "trained models", "complete deletion cannot be guaranteed", "research installation"] {
            XCTAssertTrue(limit.contains(word), word)
        }
    }

    func testRenderingWithdrawalDoesNotWriteJournalChangeConsentOrSend() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let journal = LiteWithdrawalJournal(url: folder.appendingPathComponent("request.json"))
        var calls = 0
        for phase in [LiteWithdrawalRecord.Phase.pending, .liveMediaRemoved] {
            let record = LiteWithdrawalRecord(server: "https://research.invalid", owner: String(repeating: "a", count: 32),
                phase: phase, retryAt: phase == .pending ? Date().addingTimeInterval(60) : nil,
                requestedAt: Date(timeIntervalSince1970: 1000))
            try journal.save(record)
            let before = try Data(contentsOf: journal.url)
            let model = LiteWithdrawalModel(journal: journal, accountPaused: { _ in false },
                stopUploads: { calls += 1 }, owner: { _ in calls += 1; return nil },
                send: { _, _ in calls += 1; return .liveMediaRemoved },
                sendScoped: { _, _, _ in calls += 1; return .liveMediaRemoved })
            for copy in [en, zh, en] {
                XCTAssertFalse(copy.text(model.statusTitle).isEmpty)
                if let message = model.message { XCTAssertFalse(copy.text(message).isEmpty) }
                XCTAssertTrue(model.blocksUploads)
            }
            XCTAssertEqual(try Data(contentsOf: journal.url), before)
            XCTAssertEqual(try journal.load(), record)
            XCTAssertEqual(model.finished, phase == .liveMediaRemoved)
        }
        XCTAssertEqual(calls, 0)
    }

    func testPersonalAndResearchQuotaRenderingDoesNotChangeOwnershipOrReset() throws {
        let day = 1_790_000_000, account = String(repeating: "2", count: 64), owner = String(repeating: "1", count: 32)
        let bytes = try JSONSerialization.data(withJSONObject: ["v":1,"account_sha256":account,"as_of":day,
            "resets_at":(day/86400+1)*86400,"used":1,"pending":0,"limit":2,"remaining":1,"attempt_limit_reached":false])
        let personal = try JSONDecoder().decode(LitePersonalBalance.self, from: bytes)
        let date = Date(timeIntervalSince1970: Double(day))
        try personal.validate(account: account, at: date)
        let reset = date.addingTimeInterval(60)
        let research = try XCTUnwrap(LiteCloudQuota.parse(["scope":"installation","operation":"segment","limit":5,"used":1,
            "remaining":4,"resets_at":ISO8601DateFormatter().string(from: reset)]))
        let scope = LiteCaptureResearchScope(origin:"https://research.invalid", epoch:nil)
        let receipt = LiteResearchQuotaReceipt(quota: research, scope: scope, owner: owner)
        for copy in [en, zh, en] {
            XCTAssertTrue(copy.format("%@ 次", String(personal.remaining)).contains("1"))
            XCTAssertTrue(copy.format("研究辨識剩餘 %@／%@ 次", "4", "5").contains("4"))
            XCTAssertFalse(copy.date(reset).isEmpty)
            XCTAssertEqual(receipt.current(at: date, scope: scope, registeredOwner: owner), research)
            XCTAssertNil(receipt.current(at: date, scope: scope, registeredOwner: String(repeating: "3", count: 32)))
            XCTAssertNil(receipt.current(at: reset, scope: scope, registeredOwner: owner))
            XCTAssertEqual(personal, try JSONDecoder().decode(LitePersonalBalance.self, from: bytes))
        }
        XCTAssertThrowsError(try personal.validate(account: String(repeating: "4", count: 64), at: date))
    }

    func testFailureAndUnknownStatusRemainFailuresWithoutFabricatedSuccess() {
        for failure in [LiteWithdrawalFailure.noIdentity, .invalidReceipt, .unavailable, .invalidState, .cancellationRequiresServer, .renewalUnconfirmed] {
            let source = failure.localizedDescription
            XCTAssertNotEqual(en.text(source), source)
            XCTAssertEqual(zh.text(source), source)
        }
        let unknown = "未核對的新錯誤 % @ / pending"
        XCTAssertEqual(en.text(unknown), unknown)
        XCTAssertEqual(en.text("取消"), "Cancel")
        XCTAssertEqual(LiteResearchCopy(locale: Locale(identifier: "fr_FR")).text("研究上傳"), "研究上傳")
    }

    func testRejoinFailureTranslationsKeepUploadsOffAndIdentityIntact() {
        for failure in [LiteReconsentFailure.endpointUnavailable, .authorizationRejected, .connectionUnavailable,
                        .identityUnavailable, .capacityUnavailable] {
            let source = failure.localizedDescription
            XCTAssertNotEqual(en.text(source), source)
            XCTAssertTrue(en.text(source).contains("stay off"))
            XCTAssertEqual(zh.text(source), source)
        }
        let saved = en.text("已讀到這次安裝保存的裝置登記；本次未重新測試雲端功能，也沒有傳送照片。研究同意與每筆上傳仍須各自確認。")
        XCTAssertTrue(saved.contains("saved registration")); XCTAssertTrue(saved.contains("not retested"))
        XCTAssertTrue(saved.contains("no photos"))
    }
}
