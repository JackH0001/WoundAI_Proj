import XCTest
@testable import WoundLite

private actor BalanceTransport: LiteAuthenticatedTransport {
    var owner = String(repeating: "1", count: 32)
    let data: Data
    var requests: [URLRequest] = []
    var loseOwner: Bool
    init(_ data: Data, loseOwner: Bool = false) { self.data = data; self.loseOwner = loseOwner }
    func installation() -> String { owner }
    func send(_ request: URLRequest, installation: String) throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        if loseOwner { owner = String(repeating: "3",count:32) }
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

@MainActor final class LitePersonalBalanceTests: XCTestCase {
    let account = String(repeating: "2", count: 64)
    let day = 1_790_000_000
    var date: Date { Date(timeIntervalSince1970:Double(day)) }
    var context: LiteDeletionContext {
        LiteDeletionContext(origin:"https://native.example.com",installation:String(repeating:"1",count:32),account:account)
    }
    func data(_ overrides: [String:Any] = [:]) throws -> Data {
        let values: [String:Any] = ["v":1,"account_sha256":account,"as_of":day,"resets_at":(day/86400+1)*86400,
                                    "used":1,"pending":1,"limit":2,"remaining":0,"attempt_limit_reached":false]
        return try JSONSerialization.data(withJSONObject: values.merging(overrides){_,n in n})
    }
    func testOfferRechecksAtUTCResetAndStopsAfterSecondSnapshotExpires() async throws {
        let midnight = (day / 86400 + 1) * 86400
        let free = try JSONDecoder().decode(LitePersonalBalance.self, from: data(["as_of": midnight - 2]))
        let plus = try JSONDecoder().decode(LitePersonalBalance.self, from: data([
            "as_of": midnight, "resets_at": midnight + 86400, "limit": 10, "remaining": 8
        ]))
        var received: [LiteOfferPlanStatus] = [], deadlines: [Date] = []
        var reads = 0
        await LiteOfferPlanStatus.observe(fetch: {
            reads += 1
            return reads == 1 ? free : plus
        }, waitUntil: { deadlines.append($0) }, publish: { received.append($0) })
        XCTAssertEqual(reads, 2)
        XCTAssertEqual(deadlines, [midnight, midnight + 300].map { Date(timeIntervalSince1970: Double($0)) })
        XCTAssertEqual(received, [.checking, .available(free), .checking, .available(plus), .expired])
    }

    func testFailedFirstPlanReadWaitsForExplicitRetry() async {
        var received: [LiteOfferPlanStatus] = []
        await LiteOfferPlanStatus.observe(fetch: { throw LiteAccountFailure.unavailable },
                                          waitUntil: { _ in XCTFail("Failed requests have no freshness deadline") },
                                          publish: { received.append($0) })
        XCTAssertEqual(received, [.checking, .unavailable])
    }

    func testFailedRefreshDropsPreviousOfferAndStopsReading() async throws {
        let balance = try JSONDecoder().decode(LitePersonalBalance.self, from: data())
        var received: [LiteOfferPlanStatus] = []
        await LiteOfferPlanStatus.observe(fetch: {
            if received.contains(.available(balance)) { throw LiteAccountFailure.unavailable }
            return balance
        }, waitUntil: { _ in }, publish: { received.append($0) })
        XCTAssertEqual(received, [.checking, .available(balance), .checking, .unavailable])
    }

    func testLeavingTheOfferDuringExpiryWaitPreventsSecondRead() async throws {
        let balance = try JSONDecoder().decode(LitePersonalBalance.self, from: data())
        var received: [LiteOfferPlanStatus] = []
        await Task { @MainActor in
            await LiteOfferPlanStatus.observe(fetch: { balance }, waitUntil: { _ in
                withUnsafeCurrentTask { $0?.cancel() }
            }, publish: { received.append($0) })
        }.value
        XCTAssertEqual(received, [.checking, .available(balance)])
    }

    func testCancelledPlanRequestCannotPublishLateAccountBalance() async throws {
        let balance = try JSONDecoder().decode(LitePersonalBalance.self, from: data())
        var received: [LiteOfferPlanStatus] = []
        await Task { @MainActor in
            await LiteOfferPlanStatus.observe(fetch: {
                withUnsafeCurrentTask { $0?.cancel() }
                return balance
            }, waitUntil: { _ in XCTFail("Expired account must not start a timer") },
               publish: { received.append($0) })
        }.value
        XCTAssertEqual(received, [.checking])
    }

    func testQueryUsesOnlyOriginalOwnerAndAccountWithoutPhotoOrOperation() async throws {
        let transport = try BalanceTransport(data())
        let value = try await LitePersonalBalance.fetch(context: context, transport: transport, now: { self.date })
        let requests = await transport.requests
        XCTAssertEqual(requests.count,1)
        XCTAssertEqual(requests[0].url?.absoluteString,context.origin+LitePersonalBalance.path)
        XCTAssertEqual(requests[0].httpMethod,"POST")
        let body = try XCTUnwrap(requests[0].httpBody)
        XCTAssertEqual(try JSONSerialization.jsonObject(with:body) as? [String:String],
                       ["anon_id":context.installation,"account_sha256":account])
        XCTAssertEqual(value.remaining,0)
        XCTAssertEqual(value.pending,1)
        XCTAssertTrue(value.isFresh(at:date))
        XCTAssertFalse(value.isFresh(at:date.addingTimeInterval(300)))
    }
    func testForeignAccountOldDayOrResearchQuotaCannotAppearAsPersonalBalance() throws {
        for changes: [String:Any] in [["account_sha256":String(repeating:"4",count:64)],["limit":5],
                                      ["remaining":2],["pending":31],["used":-1],["resets_at":day],
                                      ["as_of":Int.max],["as_of":day+120]] {
            let v = try JSONDecoder().decode(LitePersonalBalance.self,from:data(changes))
            XCTAssertThrowsError(try v.validate(account:account,at:date))
        }
        let paid = try JSONDecoder().decode(LitePersonalBalance.self,from:data(["limit":10,"remaining":8]))
        try paid.validate(account:account,at:date)
        XCTAssertThrowsError(try paid.validate(account:account,at:Date(timeIntervalSince1970:Double(paid.resets_at))))
        let downgraded = try JSONDecoder().decode(LitePersonalBalance.self,from:data(["used":10,"pending":0]))
        try downgraded.validate(account:account,at:date)
    }
    func testInstallationSwitchDuringQueryRejectsReply() async throws {
        let transport = try BalanceTransport(data(),loseOwner:true)
        do {
            _ = try await LitePersonalBalance.fetch(context: context, transport: transport, now: { self.date })
            XCTFail("balance escaped original installation")
        } catch { XCTAssertTrue(error is LiteAccountFailure) }
    }
    func testResearchFiveCannotCrossRegistrationOriginParticipationOrExpiry() throws {
        let reset=Date(timeIntervalSince1970:Double(day+600))
        let quota=try XCTUnwrap(LiteCloudQuota.parse(["scope":"installation","operation":"segment","limit":5,"used":1,
                "remaining":4,"resets_at":ISO8601DateFormatter().string(from: reset)]))
        let scope=LiteCaptureResearchScope(origin:"https://research.example.com",epoch:nil)
        let receipt=LiteResearchQuotaReceipt(quota:quota,scope:scope,owner:context.installation)
        XCTAssertEqual(receipt.current(at:date,scope:scope,registeredOwner:context.installation)?.remaining,4)
        XCTAssertNil(receipt.current(at:date,scope:scope,registeredOwner:nil))
        XCTAssertNil(receipt.current(at:date,scope:scope,registeredOwner:String(repeating:"9",count:32)))
        XCTAssertNil(receipt.current(at:date,scope:.init(origin:context.origin,epoch:nil),registeredOwner:context.installation))
        XCTAssertNil(receipt.current(at:date,scope:.init(origin:scope.origin,epoch:String(repeating:"a",count:32)),registeredOwner:context.installation))
        XCTAssertNil(receipt.current(at:date,scope:nil,registeredOwner:context.installation))
        XCTAssertNil(receipt.current(at:reset,scope:scope,registeredOwner:context.installation))
    }

    func testOfferSeparatesAttemptsPendingCompletedAndRemainingUses() throws {
        let cases: [([String: Any], String)] = [
            (["attempt_limit_reached": true], "今日辨識嘗試已達上限"),
            ([:], "剩餘名額正由處理中的辨識使用，完成後可再查看。"),
            (["used": 2, "pending": 0], "今天的辨識額度已用完。"),
            (["limit": 10, "remaining": 8], "今天還可辨識 8 次")
        ]
        for (fields, expected) in cases {
            let value = try JSONDecoder().decode(LitePersonalBalance.self, from: data(fields))
            try value.validate(account: account, at: date)
            XCTAssertEqual(value.availabilityMessage(locale: Locale(identifier: "zh-Hant")), expected)
            XCTAssertNotEqual(value.availabilityMessage(locale: Locale(identifier: "en")), expected)
        }
    }

    func testOnlyExhaustedFreeAccountWithAvailablePlusCapacityGetsUpgradeLink() throws {
        let cases: [([String: Any], Bool)] = [
            (["used": 2, "pending": 0], true),
            (["used": 9, "pending": 0], true),
            (["used": 10, "pending": 0], false),
            (["used": 2, "pending": 0, "attempt_limit_reached": true], false),
            (["used": 10, "pending": 0, "limit": 10], false),
            (["used": 1, "pending": 0, "remaining": 1], false),
            ([:], false)
        ]
        for (fields, expected) in cases {
            let value = try JSONDecoder().decode(LitePersonalBalance.self, from: data(fields))
            try value.validate(account: account, at: date)
            XCTAssertEqual(value.canGainUsesWithPlus, expected)
        }
    }

    func testPendingShortcutRequiresOccupiedSlotsRatherThanCompletedOrAttemptLimit() throws {
        for (overrides, expected): ([String: Any], Bool) in [
            ([:], true), (["used": 0, "pending": 2], true), (["limit": 10, "used": 9], true),
            (["attempt_limit_reached": true], false), (["used": 2], false),
            (["remaining": 1, "used": 0], false), (["used": 2, "pending": 0], false)
        ] {
            let value = try JSONDecoder().decode(LitePersonalBalance.self, from: data(overrides))
            try value.validate(account: account, at: date)
            XCTAssertEqual(value.waitingForReservedUses, expected)
        }
    }

    func testPendingShortcutRequiresAdmittedLocalOperationOnSameAccountInstallationOriginAndDay() throws {
        let balance = try JSONDecoder().decode(LitePersonalBalance.self, from: data())
        let scope = LitePendingCaptureScope(balance: balance, account: .init(generation: 1, token: "test"), context: context)
        let op = String(repeating: "a", count: 32)
        let digest = String(repeating: "b", count: 64)
        func make(owner: String? = nil, installation: String? = nil, origin: String? = nil,
                  offset: Int = 0, state: String = "ready", action: String = "run",
                  outcome: String = "reserved") throws -> LiteServiceRecord {
            let utc = day / 86400 + offset
            let receipt = LiteServiceReceipt(day: utc, operation: op, request_digest: digest, outcome: outcome,
                                             result_sha256: nil, grant_sha256: digest, admitted_limit: 2)
            var record = LiteServiceRecord(captureID: op, operation: op, origin: origin ?? context.origin,
                installation: installation ?? context.installation, imageSHA256: digest, width: 10, height: 20)
            record.accountSHA256 = owner
            record.day = utc
            record.requestDigest = digest
            record.next = action
            record.reply = .init(state: state, operation: op, day: utc, retry_action: action, research_stored: false,
                receipt: receipt, result: nil, result_json: nil, service_result_stored: false, result_expires: (utc + 1) * 86400)
            try record.validate()
            return record
        }
        XCTAssertTrue(scope.contains(try make(owner: account)))
        XCTAssertTrue(scope.contains(try make(owner: account, state: "pending", action: "status", outcome: "running")))
        XCTAssertFalse(scope.contains(try make()))
        XCTAssertFalse(scope.contains(try make(owner: String(repeating: "f", count: 64))))
        XCTAssertFalse(scope.contains(try make(owner: account, installation: String(repeating: "e", count: 32))))
        XCTAssertFalse(scope.contains(try make(owner: account, origin: "https://other.example.com")))
        XCTAssertFalse(scope.contains(try make(owner: account, offset: -1)))
        XCTAssertFalse(scope.contains(try make(owner: account, state: "cancelled", action: "none", outcome: "cancelled")))
        var unsent = try make(owner: account)
        unsent.reply = nil
        XCTAssertFalse(scope.contains(unsent))
    }

    func testOnlyCurrentDaySnapshotIsFreshAtUTCReset() throws {
        let end=(day/86400+1)*86400, stamp=end-3
        let v = try JSONDecoder().decode(LitePersonalBalance.self,from:data(["as_of":stamp,"attempt_limit_reached":true]))
        let now=Date(timeIntervalSince1970:Double(stamp))
        try v.validate(account:account,at:now)
        XCTAssertTrue(v.isFresh(at:now)); XCTAssertFalse(v.isFresh(at:now.addingTimeInterval(3)))
    }
}
