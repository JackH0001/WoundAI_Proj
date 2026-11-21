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

    func testOnlyCurrentDaySnapshotIsFreshAtUTCReset() throws {
        let end=(day/86400+1)*86400, stamp=end-3
        let v = try JSONDecoder().decode(LitePersonalBalance.self,from:data(["as_of":stamp,"attempt_limit_reached":true]))
        let now=Date(timeIntervalSince1970:Double(stamp))
        try v.validate(account:account,at:now)
        XCTAssertTrue(v.isFresh(at:now)); XCTAssertFalse(v.isFresh(at:now.addingTimeInterval(3)))
    }
}
