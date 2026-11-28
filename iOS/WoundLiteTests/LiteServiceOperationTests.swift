import XCTest
import CryptoKit
import UIKit
@testable import WoundLite

private struct ServiceRecoveryFixture: Decodable {
    let installation: String, capture_id: String, jpeg: String, image_sha256: String
    let available: LiteServiceReply, authorization: LiteServiceReply, prepare: LiteServiceReply, completed: LiteServiceReply
    let signature_binding: [String:String]
    static func load() throws -> Self {
        let u=try XCTUnwrap(Bundle(for:LiteServiceOperationTests.self).url(forResource:"lite_service_recovery",withExtension:"json"))
        return try JSONDecoder().decode(Self.self,from:Data(contentsOf:u))
    }
    var image: Data { Data(base64Encoded:jpeg)! }
    func bytes(_ reply:LiteServiceReply) throws -> Data { try JSONEncoder().encode(reply) }
}

extension LiteServiceOperationTests {
    private func recoverySetup() async throws -> (ServiceRecoveryFixture,ServiceWire,ServiceMemory,LiteServiceClient) {
        let f=try ServiceRecoveryFixture.load(),w=ServiceWire(owner:f.installation),m=ServiceMemory(),c=try client(w,m)
        _=try await c.enqueue(captureID:f.capture_id,jpeg:f.image)
        return (f,w,m,c)
    }
    private func recoveryStart(_ f:ServiceRecoveryFixture) throws -> [ServiceStep] {
        [ServiceStep(action:"prepare",data:try f.bytes(f.available)),
         ServiceStep(action:"recover",data:try f.bytes(f.authorization))]
    }
    private func recoveryFinish(_ f:ServiceRecoveryFixture) throws -> [ServiceStep] {
        [ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),
         ServiceStep(action:"run",data:try f.bytes(f.completed))]
    }
    func testCrossDayGoldenAdoptsOneGenerationBeforeSendingSameImage() async throws {
        let (f,w,m,c)=try await recoverySetup(),image=f.image
        try await w.plan(recoveryStart(f)+recoveryFinish(f))
        let ready=try await c.resume(f.capture_id) { _ in image }
        XCTAssertEqual(ready.operation,f.prepare.operation);XCTAssertEqual(ready.next,"run")
        XCTAssertEqual(ready.recoveryHistory?.count,1);XCTAssertEqual(ready.version,2)
        let done=try await client(w,m).resume(f.capture_id) { id in
            XCTAssertEqual(id,f.capture_id);return image
        }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.reply,f.completed)
        let sent=await w.requests();XCTAssertEqual(sent.map{$0.url!.lastPathComponent},["prepare","recover","prepare","run"])
        XCTAssertEqual(sent.filter{$0.url!.lastPathComponent=="run"}.count,1)
        XCTAssertNotNil(sent.last!.httpBody!.range(of:image))
        XCTAssertEqual(done.recoveryHistory?.first?.authorization.parent_operation,f.capture_id)
        _=try await client(w,m).resume(f.capture_id) { _ in XCTFail();return nil }
        let again=await w.requests();XCTAssertEqual(again.count,4)
    }
    func testLostRecoveryResponseAfterRestartReadsAuthorizationInsteadOfNewPhoto() async throws {
        let (f,w,m,c)=try await recoverySetup(),image=f.image
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.available)),ServiceStep(action:"recover",data:Data(),networkError:.timedOut)])
        let waiting=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        XCTAssertEqual(waiting.operation,f.capture_id);XCTAssertNil(waiting.recoveryHistory)
        try await w.plan([ServiceStep(action:"status",data:f.bytes(f.authorization))]+recoveryFinish(f))
        let done=try await client(w,m,1006).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory?.count,1)
        let sent=await w.requests();XCTAssertEqual(sent.filter{$0.url!.lastPathComponent=="run"}.count,1)
    }
    func testJournalFailureBeforeAdoptionCannotDispatchSuccessor() async throws {
        let (f,w,m,c)=try await recoverySetup(),image=f.image
        try await w.plan(recoveryStart(f));await m.fail(5)
        do { _=try await c.resume(f.capture_id) { _ in XCTFail();return nil };XCTFail() } catch { }
        let stored=try await client(w,m).stored(f.capture_id)
        XCTAssertEqual(stored.operation,f.capture_id);XCTAssertNil(stored.recoveryHistory)
        let before=await w.requests();XCTAssertEqual(before.count,2)
        try await w.plan([ServiceStep(action:"status",data:f.bytes(f.authorization))]+recoveryFinish(f))
        let done=try await client(w,m).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory?.count,1)
    }
    func testJournalFailureAfterAdoptionResumesSameSuccessor() async throws {
        let (f,w,m,c)=try await recoverySetup(),image=f.image
        try await w.plan(recoveryStart(f));await m.fail(6)
        do { _=try await c.resume(f.capture_id) { _ in nil };XCTFail() } catch { }
        let stored=try await client(w,m).stored(f.capture_id)
        XCTAssertEqual(stored.operation,f.prepare.operation);XCTAssertEqual(stored.recoveryHistory?.count,1)
        try await w.plan(recoveryFinish(f))
        let done=try await client(w,m).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory,stored.recoveryHistory)
    }
    func testChangedRecoveryBindingStopsWithoutAdopting() async throws {
        for field in ["parent_operation","image_sha256","successor_operation","parent_day","successor_day"] {
            let (f,w,_,c)=try await recoverySetup()
            var response=try JSONSerialization.jsonObject(with:f.bytes(f.authorization)) as! [String:Any]
            var auth=response["recovery"] as! [String:Any]
            switch field {
            case "parent_day","successor_day":auth[field] = -1
            case "successor_operation":auth[field]=f.capture_id
            default:auth[field]=String(repeating:"b",count:field=="parent_operation" ? 32:64)
            }
            response["recovery"]=auth
            await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.available)),ServiceStep(action:"recover",data:try JSONSerialization.data(withJSONObject:response))])
            let r=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
            XCTAssertEqual(r.note,"review");XCTAssertEqual(r.operation,f.capture_id);XCTAssertNil(r.recoveryHistory)
        }
    }
    func testExistingPinnedReceiptCannotBecomeSuccessor() async throws {
        let (f,w,m,c)=try await setup(),image=f.image
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:Data(),networkError:.timedOut)])
        _=try await c.resume(f.capture_id) { _ in image }
        let recovery=try ServiceRecoveryFixture.load()
        await w.plan([ServiceStep(action:"status",data:try recovery.bytes(recovery.authorization))])
        let r=try await client(w,m,1006).resume(f.capture_id) { _ in XCTFail();return nil }
        XCTAssertEqual(r.note,"review");XCTAssertEqual(r.operation,f.capture_id);XCTAssertNil(r.recoveryHistory)
    }
    func testFileJournalRestartKeepsRetiredParentAndCurrentGeneration() async throws {
        let f=try ServiceRecoveryFixture.load(),w=ServiceWire(owner:f.installation),image=f.image
        let dir=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:dir) }
        let c=try client(w,LiteServiceFileJournal(directory:dir))
        _=try await c.enqueue(captureID:f.capture_id,jpeg:image)
        try await w.plan(recoveryStart(f)+recoveryFinish(f))
        let before=try await c.resume(f.capture_id) { _ in image }
        let restarted=try client(w,LiteServiceFileJournal(directory:dir))
        let saved=try await restarted.stored(f.capture_id);XCTAssertEqual(saved,before)
        let done=try await restarted.resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory,before.recoveryHistory)
    }
    func testRecoverySignatureMatchesPythonGoldenBytes() throws {
        let row=try ServiceRecoveryFixture.load().signature_binding
        let data=try LiteAttestRequest.clientData(audience:row["audience"]!,installation:row["installation"]!,
            keyID:Data(base64Encoded:row["key_id"]!)!,challenge:Data(base64Encoded:row["challenge"]!)!,
            requestID:row["request_id"]!,method:row["method"]!,path:row["path"]!,contentType:row["content_type"]!,
            body:Data(base64Encoded:row["body"]!)!)
        XCTAssertEqual(data.base64EncodedString(),row["expected_client_data"])
        XCTAssertEqual(LiteServiceBytes.hash(data),row["expected_sha256"])
    }
}

private struct ServiceResubmitFixture: Decodable {
    let installation: String, capture_id: String, jpeg: String
    let cancelled: LiteServiceReply, authorization: LiteServiceReply, prepare: LiteServiceReply, completed: LiteServiceReply
    let signature_binding: [String:String]
    var image: Data { Data(base64Encoded:jpeg)! }
    func bytes(_ v:LiteServiceReply) throws -> Data { try JSONEncoder().encode(v) }
    static func load() throws -> Self {
        let u=try XCTUnwrap(Bundle(for:LiteServiceOperationTests.self).url(forResource:"lite_service_resubmit",withExtension:"json"))
        return try JSONDecoder().decode(Self.self,from:Data(contentsOf:u))
    }
}

extension LiteServiceOperationTests {
    private func expiredSetup(reason:String? = "reservation_expired") async throws -> (ServiceResubmitFixture,ServiceWire,ServiceMemory,LiteServiceClient) {
        let f=try ServiceResubmitFixture.load(),w=ServiceWire(owner:f.installation),m=ServiceMemory(),c=try client(w,m)
        _=try await c.enqueue(captureID:f.capture_id,jpeg:f.image)
        var cancelled=f.cancelled;cancelled.cancel_reason=reason
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(cancelled))])
        _=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        return (f,w,m,c)
    }
    private func resubmitSteps(_ f:ServiceResubmitFixture) throws -> [ServiceStep] {
        [ServiceStep(action:"resubmit",data:try f.bytes(f.authorization)),
         ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),
         ServiceStep(action:"run",data:try f.bytes(f.completed))]
    }
    func testLeaseExpiryShowsExplicitResubmitWithoutAutomaticNetwork() async throws {
        let (f,w,m,c)=try await expiredSetup()
        let r=try await c.stored(f.capture_id)
        XCTAssertEqual(r.note,"reservationExpired");XCTAssertTrue(r.canResubmit)
        XCTAssertTrue(r.message.contains("預留已逾時"));XCTAssertFalse(r.message.contains("未通過"))
        _=try await client(w,m).resume(f.capture_id) { _ in XCTFail();return nil }
        let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testExplicitResubmitPersistsIntentAndSuccessorBeforeSending() async throws {
        let (f,w,m,c)=try await expiredSetup(),image=f.image
        let intent=try await c.requestResubmission(f.capture_id)
        XCTAssertEqual(intent.operation,f.capture_id);XCTAssertEqual(intent.next,"resubmit")
        XCTAssertTrue(intent.resubmitRequested == true);XCTAssertFalse(intent.canResubmit)
        let before=await w.requests();XCTAssertEqual(before.count,1)
        try await w.plan(resubmitSteps(f))
        let done=try await client(w,m).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory?.count,1)
        XCTAssertEqual(done.recoveryHistory?.first?.priorReply,f.cancelled)
        XCTAssertEqual(done.recoveryHistory?.first?.authorization.version,2)
        XCTAssertNil(done.resubmitRequested)
        _=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        let sent=await w.requests();XCTAssertEqual(sent.map{$0.url!.lastPathComponent},["prepare","resubmit","prepare","run"])
    }
    func testPreflightRejectedAndUnknownCancelledNeverOfferResubmit() async throws {
        for reason in ["preflight_rejected","cancelled_unknown",nil] {
            let (f,w,_,c)=try await expiredSetup(reason:reason)
            let r=try await c.stored(f.capture_id);XCTAssertFalse(r.canResubmit)
            XCTAssertEqual(r.note,reason == "preflight_rejected" ? "rejected":"cancelled")
            do { _=try await c.requestResubmission(f.capture_id);XCTFail() } catch { }
            let sent=await w.requests();XCTAssertEqual(sent.count,1)
        }
    }
    func testIntentSaveFailureCannotSendOrChangeOperation() async throws {
        let (f,w,m,c)=try await expiredSetup();await m.fail(4)
        do { _=try await c.requestResubmission(f.capture_id);XCTFail() } catch { }
        let r=try await c.stored(f.capture_id);XCTAssertNil(r.resubmitRequested);XCTAssertTrue(r.canResubmit)
        let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testLostResubmitReplyRestartsWithSameExplicitIntent() async throws {
        let (f,w,m,c)=try await expiredSetup(),image=f.image
        _=try await c.requestResubmission(f.capture_id)
        await w.plan([ServiceStep(action:"resubmit",data:Data(),networkError:.timedOut)])
        let waiting=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        XCTAssertEqual(waiting.operation,f.capture_id);XCTAssertTrue(waiting.resubmitRequested == true)
        try await w.plan([ServiceStep(action:"status",data:f.bytes(f.cancelled))]+resubmitSteps(f))
        let ready=try await client(w,m,1006).resume(f.capture_id) { _ in image }
        XCTAssertEqual(ready.next,"run")
        let done=try await client(w,m,1006).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed");XCTAssertEqual(done.recoveryHistory?.count,1)
        let sent=await w.requests();XCTAssertEqual(sent.filter{$0.url!.lastPathComponent=="run"}.count,1)
    }
    func testSuccessorSaveFailureKeepsParentAndExplicitIntent() async throws {
        let (f,w,m,c)=try await expiredSetup(),image=f.image
        _=try await c.requestResubmission(f.capture_id);await m.fail(6)
        await w.plan([ServiceStep(action:"resubmit",data:try f.bytes(f.authorization))])
        do { _=try await c.resume(f.capture_id) { _ in XCTFail();return nil };XCTFail() } catch { }
        let r=try await c.stored(f.capture_id)
        XCTAssertEqual(r.operation,f.capture_id);XCTAssertNil(r.recoveryHistory)
        try await w.plan([ServiceStep(action:"status",data:f.bytes(f.cancelled))]+resubmitSteps(f))
        _=try await c.resume(f.capture_id) { _ in image }
        let done=try await c.resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note,"confirmed")
        let sent=await w.requests();XCTAssertEqual(sent.filter{$0.url!.lastPathComponent=="run"}.count,1)
    }
    func testResubmitRechecksQuotaWithoutNewPhotoOrMoreOperations() async throws {
        let (f,w,_,c)=try await expiredSetup()
        _=try await c.requestResubmission(f.capture_id)
        await w.plan([ServiceStep(action:"resubmit",data:try f.bytes(f.authorization)),
                      ServiceStep(action:"prepare",data:error("lite_usage_limit"),code:429,wait:"30")])
        let r=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        XCTAssertEqual(r.note,"quota");XCTAssertEqual(r.operation,f.prepare.operation)
        XCTAssertEqual(r.recoveryHistory?.count,1);XCTAssertFalse(r.canResubmit)
        let sent=await w.requests();XCTAssertEqual(sent.count,3)
    }
    func testResubmitBindingMismatchCannotAdopt() async throws {
        for field in ["parent_request_digest","fence_sha256","reason"] {
            let (f,w,_,c)=try await expiredSetup()
            _=try await c.requestResubmission(f.capture_id)
            var reply=try JSONSerialization.jsonObject(with:f.bytes(f.authorization)) as! [String:Any]
            var auth=reply["recovery"] as! [String:Any];auth[field]=field == "reason" ? "preflight_rejected":String(repeating:"e",count:64);reply["recovery"]=auth
            await w.plan([ServiceStep(action:"resubmit",data:try JSONSerialization.data(withJSONObject:reply))])
            let r=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
            XCTAssertEqual(r.note,"review");XCTAssertEqual(r.operation,f.capture_id)
        }
    }
    func testUnrequestedResubmitAuthorizationIsRejected() async throws {
        let f=try ServiceResubmitFixture.load(),w=ServiceWire(owner:f.installation),m=ServiceMemory(),c=try client(w,m)
        _=try await c.enqueue(captureID:f.capture_id,jpeg:f.image)
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.authorization))])
        let r=try await c.resume(f.capture_id) { _ in XCTFail();return nil }
        XCTAssertEqual(r.note,"review");XCTAssertEqual(r.operation,f.capture_id)
    }
    func testDoubleResubmitTapCannotPersistSecondIntent() async throws {
        let (f,w,_,c)=try await expiredSetup()
        let r=try await c.requestResubmission(f.capture_id)
        do { _=try await c.requestResubmission(f.capture_id);XCTFail() } catch { }
        let stored=try await c.stored(f.capture_id);XCTAssertEqual(stored,r)
        let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testResubmitSignatureMatchesPythonGoldenBytes() throws {
        let row=try ServiceResubmitFixture.load().signature_binding
        let data=try LiteAttestRequest.clientData(audience:row["audience"]!,installation:row["installation"]!,
            keyID:Data(base64Encoded:row["key_id"]!)!,challenge:Data(base64Encoded:row["challenge"]!)!,
            requestID:row["request_id"]!,method:row["method"]!,path:row["path"]!,contentType:row["content_type"]!,
            body:Data(base64Encoded:row["body"]!)!)
        XCTAssertEqual(data.base64EncodedString(),row["expected_client_data"])
        XCTAssertEqual(LiteServiceBytes.hash(data),row["expected_sha256"])
    }
}

private struct ServiceFixture: Decodable {
    let installation: String, capture_id: String, jpeg: String, image_sha256: String
    let prepare: LiteServiceReply, completed: LiteServiceReply
    let signature_bindings: [[String:String]]
    static func load() throws -> Self {
        let url=try XCTUnwrap(Bundle(for:LiteServiceOperationTests.self).url(forResource:"lite_service_operation",withExtension:"json"))
        return try JSONDecoder().decode(Self.self,from:Data(contentsOf:url))
    }
    var image: Data { Data(base64Encoded:jpeg)! }
    func bytes(_ value: LiteServiceReply) throws -> Data { try JSONEncoder().encode(value) }
    func changed(_ change: (inout [String:Any]) -> Void) throws -> Data {
        var value=try JSONSerialization.jsonObject(with:bytes(completed)) as! [String:Any]
        change(&value); return try JSONSerialization.data(withJSONObject:value)
    }
}

private actor ServiceMemory: LiteServiceJournal {
    var entries=[String:Data](), writes=0, failAt=0
    func load(_ id: String) -> Data? { entries[id] }
    func replace(_ id: String, expected: Data?, with data: Data) throws {
        writes += 1
        if writes == failAt { throw LiteServiceFailure.invalidState }
        guard entries[id] == expected else { throw LiteServiceFailure.conflict };entries[id]=data
    }
    func fail(_ n:Int) { failAt=n }
    func corrupt(_ id:String,_ data:Data) { entries[id]=data }
}

private actor VerifiedServiceWire: LiteAccountBoundServiceTransport {
    let wire: ServiceWire
    let account = String(repeating: "a", count: 64)
    private(set) var actions: [String] = []
    private(set) var expectations: [String?] = []
    init(_ wire: ServiceWire) { self.wire = wire }
    func installation() async throws -> String { await wire.installation() }
    func verifyRecognitionAccount(_ expected: String, installation: String) async throws {
        guard expected == account, await wire.installation() == installation else { throw LiteServiceFailure.conflict }
    }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        XCTFail("Account-aware transport must verify every recognition request")
        throw LiteServiceFailure.conflict
    }
    func sendRecognition(_ request: URLRequest, installation: String, expectedAccount: String?) async throws
        -> (data: Data, http: HTTPURLResponse, accountSHA256: String) {
        actions.append(request.url!.lastPathComponent)
        expectations.append(expectedAccount)
        if let expectedAccount { try await verifyRecognitionAccount(expectedAccount, installation: installation) }
        let reply = try await wire.send(request, installation: installation)
        return (reply.0, reply.1, account)
    }
}

private struct ServiceStep: Sendable {
    let action: String, data: Data
    var code=200
    var networkError: URLError.Code?
    var wait: String?
    var delay=false
    var contentType: String?
}
private actor ServiceWire: LiteAuthenticatedTransport {
    var owner: String
    var steps=[ServiceStep](),sent=[URLRequest]()
    init(owner:String) { self.owner=owner }
    func installation() -> String { owner }
    func changeOwner() { owner="changed-installation" }
    func plan(_ list:[ServiceStep]) { steps=list }
    func requests() -> [URLRequest] { sent }
    func send(_ request:URLRequest,installation:String) async throws -> (Data,HTTPURLResponse) {
        sent.append(request)
        guard owner == installation, !steps.isEmpty else { throw LiteServiceFailure.invalidState }
        let s=steps.removeFirst()
        guard request.url?.lastPathComponent == s.action else { throw LiteServiceFailure.invalidState }
        if s.delay { try await Task.sleep(nanoseconds:100_000_000) }
        if let networkError = s.networkError { throw URLError(networkError) }
        var headers = [String: String]()
        headers["Retry-After"] = s.wait
        headers["Content-Type"] = s.contentType
        return (s.data,HTTPURLResponse(url:request.url!,statusCode:s.code,httpVersion:nil,headerFields:headers)!)
    }
}

@MainActor
final class LiteServiceOperationTests: XCTestCase {
    private let origin=URL(string:"https://lite.invalid")!
    private func client(_ wire:any LiteAuthenticatedTransport,_ store:any LiteServiceJournal,_ time:Double=1000) throws -> LiteServiceClient {
        try LiteServiceClient(origin:origin,transport:wire,journal:store,now:{ Date(timeIntervalSince1970:time) })
    }
    private func setup() async throws -> (ServiceFixture,ServiceWire,ServiceMemory,LiteServiceClient) {
        let f=try ServiceFixture.load(),w=ServiceWire(owner:f.installation),m=ServiceMemory()
        let c=try client(w,m);_=try await c.enqueue(captureID:f.capture_id,jpeg:f.image)
        return (f,w,m,c)
    }
    private func doneSteps(_ f:ServiceFixture) throws -> [ServiceStep] {
        [ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:try f.bytes(f.completed))]
    }
    private func error(_ name:String) -> Data { try! JSONSerialization.data(withJSONObject:["error":name]) }

    func testVerifiedAccountSurvivesResubmissionAndSuccessorPrepare() async throws {
        let f = try ServiceResubmitFixture.load(), wire = ServiceWire(owner: f.installation)
        let checked = VerifiedServiceWire(wire), memory = ServiceMemory()
        let service = try client(checked, memory), image = f.image
        _ = try await service.enqueue(captureID: f.capture_id, jpeg: image)
        await wire.plan([ServiceStep(action: "prepare", data: try f.bytes(f.cancelled))])
        let expired = try await service.resume(f.capture_id) { _ in XCTFail(); return nil }
        let account = String(repeating: "a", count: 64)
        XCTAssertEqual(expired.accountSHA256, account)
        _ = try await service.requestResubmission(f.capture_id)
        try await wire.plan(resubmitSteps(f))
        let done = try await client(checked, memory).resume(f.capture_id) { _ in image }
        XCTAssertEqual(done.note, "confirmed")
        XCTAssertEqual(done.accountSHA256, account)
        let actions = await checked.actions, expected = await checked.expectations
        XCTAssertEqual(actions, ["prepare", "resubmit", "prepare", "run"])
        XCTAssertEqual(expected, [nil, account, account, account])
    }

    func testBoundReceiptRefusesLegacyTransportWithoutDispatch() async throws {
        let (f, wire, memory, _) = try await setup(), checked = VerifiedServiceWire(wire)
        await wire.plan([ServiceStep(action: "prepare", data: try f.bytes(f.prepare)),
                         ServiceStep(action: "run", data: Data(), networkError: .timedOut)])
        let image = f.image
        let interrupted = try await client(checked, memory).resume(f.capture_id) { _ in image }
        XCTAssertEqual(interrupted.accountSHA256, String(repeating: "a", count: 64))
        let blocked = try await client(wire, memory, 1010).resume(f.capture_id) { _ in XCTFail(); return nil }
        XCTAssertEqual(blocked.accountSHA256, interrupted.accountSHA256)
        XCTAssertEqual(blocked.note, "unavailable")
        let requests = await wire.requests()
        XCTAssertEqual(requests.map { $0.url!.lastPathComponent }, ["prepare", "run"])
    }

    func testUnauthenticatedOrInvalidPrepareReceiptDoesNotBind() async throws {
        let (f, wire, memory, service) = try await setup(), image = f.image
        let original = try await service.stored(f.capture_id)
        let raw = try XCTUnwrap(try JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        XCTAssertNil(raw["accountSHA256"])
        try await wire.plan(doneSteps(f))
        let legacy = try await service.resume(f.capture_id) { _ in image }
        XCTAssertNil(legacy.accountSHA256)
        let restarted = try await client(VerifiedServiceWire(wire), memory).resume(f.capture_id) { _ in XCTFail(); return nil }
        XCTAssertEqual(restarted, legacy)
        let (other, badWire, badMemory, _) = try await setup()
        let invalid = try other.changed { $0["operation"] = String(repeating: "f", count: 32) }
        await badWire.plan([ServiceStep(action: "prepare", data: invalid)])
        let rejected = try await client(VerifiedServiceWire(badWire), badMemory).resume(other.capture_id) { _ in XCTFail(); return nil }
        XCTAssertEqual(rejected.note, "review")
        XCTAssertNil(rejected.accountSHA256)
    }

    func testPythonGoldenResponsePersistsResultAndExactImageOnce() async throws {
        let (f,w,m,c)=try await setup();try await w.plan(doneSteps(f));let image=f.image
        let result=try await c.resume(f.capture_id) { _ in image }
        XCTAssertEqual(result.note,"confirmed");XCTAssertEqual(result.reply?.result,f.completed.result)
        XCTAssertTrue(result.message.contains("尚未存入量測紀錄或研究庫"))
        let sent=await w.requests();XCTAssertEqual(sent.map { $0.url!.lastPathComponent },["prepare","run"])
        let body=try XCTUnwrap(sent.last?.httpBody);XCTAssertNotNil(body.range(of:image))
        for forbidden in ["raw_depth","consent_epoch","paid","research_consent"] { XCTAssertNil(body.range(of:Data(forbidden.utf8))) }
        let stored=try await client(w,m).stored(f.capture_id);XCTAssertEqual(stored,result)
    }

    func testSavedPreviewRequiresTheExactCompletedPhotoEvenAtIdenticalDimensions() async throws {
        let (fixture, wire, _, client) = try await setup()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LocalImageStore(directory: directory)
        let jpeg = fixture.image
        let original = try XCTUnwrap(store.save(jpeg: jpeg))
        let ready = try await client.stored(fixture.capture_id)
        let pendingImage = await ImageLoadQueue.shared.verifiedServiceImage(journal: ready, store: store, name: original)
        XCTAssertNil(pendingImage)
        try await wire.plan(doneSteps(fixture))
        let completed = try await client.resume(fixture.capture_id) { _ in jpeg }
        let sameSize = CGSize(width: completed.width, height: completed.height)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let replacement = UIGraphicsImageRenderer(size: sameSize, format: format).image { ctx in
            UIColor.cyan.setFill()
            ctx.fill(CGRect(origin: .zero, size: sameSize))
        }
        let otherJPEG = try XCTUnwrap(replacement.jpegData(compressionQuality: 0.92))
        let other = try XCTUnwrap(store.save(jpeg: otherJPEG))
        XCTAssertNotEqual(LiteServiceBytes.hash(jpeg), LiteServiceBytes.hash(otherJPEG))
        let originalImage = await ImageLoadQueue.shared.verifiedServiceImage(journal: completed, store: store, name: original)
        let otherImage = await ImageLoadQueue.shared.verifiedServiceImage(journal: completed, store: store, name: other)
        let missing = await ImageLoadQueue.shared.verifiedServiceImage(journal: completed, store: store, name: "missing")
        XCTAssertNotNil(originalImage)
        XCTAssertNil(otherImage)
        XCTAssertNil(missing)
        XCTAssertEqual(store.rawBytes(original), jpeg)
        XCTAssertEqual(store.rawBytes(other), otherJPEG)
        let reread = try await client.stored(fixture.capture_id)
        XCTAssertEqual(reread, completed)
        let sent = await wire.requests()
        XCTAssertEqual(sent.map { $0.url?.lastPathComponent }, ["prepare", "run"])
    }
    func testCompletedOperationIsLocalReadWithoutImageOrNetwork() async throws {
        let (f,w,m,c)=try await setup();try await w.plan(doneSteps(f));let image=f.image
        _=try await c.resume(f.capture_id) { _ in image }
        let result=try await client(w,m).resume(f.capture_id) { _ in throw LiteServiceFailure.missingImage }
        XCTAssertEqual(result.note,"confirmed");let requests=await w.requests();XCTAssertEqual(requests.count,2)
    }
    func testLostRunResponseAfterRestartUsesStatusWithoutImage() async throws {
        let (f,w,m,c)=try await setup();let image=f.image
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:Data(),networkError:.timedOut)])
        let waiting=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(waiting.next,"status")
        await w.plan([ServiceStep(action:"status",data:try f.bytes(f.completed))])
        let result=try await client(w,m,1006).resume(f.capture_id) { _ in throw LiteServiceFailure.missingImage }
        XCTAssertEqual(result.note,"confirmed")
        let sent=await w.requests();XCTAssertEqual(sent.count,3);XCTAssertLessThan(sent[2].httpBody!.count,200)
        XCTAssertNil(sent[2].httpBody!.range(of:image));XCTAssertEqual(result.operation,f.capture_id)
    }
    func testLostPrepareAndUnknownStatusReuseSameOperation() async throws {
        let (f,w,m,c)=try await setup();let image=f.image
        await w.plan([ServiceStep(action:"prepare",data:Data(),networkError:.timedOut)])
        _=try await c.resume(f.capture_id) { _ in image }
        await w.plan([ServiceStep(action:"status",data:error("lite_operation_unknown"),code:404)]+(try doneSteps(f)))
        let result=try await client(w,m,1006).resume(f.capture_id) { _ in image }
        XCTAssertEqual(result.note,"confirmed")
        let sent=await w.requests();let a=try JSONSerialization.jsonObject(with:sent[0].httpBody!) as! [String:String]
        let b=try JSONSerialization.jsonObject(with:sent[2].httpBody!) as! [String:String];XCTAssertEqual(a,b)
    }
    func testPreparedStatusAllowsSendingOnlyWhenServerSaysReady() async throws {
        let (f,w,m,c)=try await setup();let image=f.image
        await w.plan([ServiceStep(action:"prepare",data:Data(),networkError:.timedOut)])
        _=try await c.resume(f.capture_id) { _ in image }
        await w.plan([ServiceStep(action:"status",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:try f.bytes(f.completed))])
        let result=try await client(w,m,1006).resume(f.capture_id) { _ in image };XCTAssertEqual(result.note,"confirmed")
    }
    func testPendingResponseDoesNotSpinOrResendImage() async throws {
        let (f,w,_,c)=try await setup();let image=f.image
        var pending=try JSONSerialization.jsonObject(with:f.bytes(f.prepare)) as! [String:Any]
        pending["state"]="pending";pending["retry_action"]="status"
        var receipt=pending["receipt"] as! [String:Any];receipt["outcome"]="running";pending["receipt"]=receipt
        await w.plan([ServiceStep(action:"prepare",data:try JSONSerialization.data(withJSONObject:pending),code:202)])
        let r=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(r.note,"pending")
        _=try await c.resume(f.capture_id) { _ in throw LiteServiceFailure.missingImage }
        let sent=await w.requests();XCTAssertEqual(sent.count,1);XCTAssertEqual(r.retryAt,1005)
    }
    func testEnqueueCannotReplaceImageOrRecreateExistingOperation() async throws {
        let (f,w,_,c)=try await setup()
        let original=try await c.stored(f.capture_id)
        let same=try await c.enqueue(captureID:f.capture_id,jpeg:f.image);XCTAssertEqual(same,original)
        do { _=try await c.enqueue(captureID:f.capture_id,jpeg:f.image+Data([0]));XCTFail() } catch { }
        let restored=try await c.stored(f.capture_id);XCTAssertEqual(restored,original)
        let sent=await w.requests();XCTAssertTrue(sent.isEmpty)
    }
    func testMissingOrDifferentLocalImageStopsBeforeUpload() async throws {
        let (f,w,_,c)=try await setup()
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare))])
        let r=try await c.resume(f.capture_id) { _ in Data([1,2,3]) }
        XCTAssertEqual(r.note,"missingImage");XCTAssertEqual(r.next,"none")
        let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testTamperedResultHashDoesNotClaimCompletion() async throws {
        let (f,w,_,c)=try await setup();let image=f.image
        let corrupt=try f.changed { $0["result_json"]="{}" }
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:corrupt)])
        let r=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(r.note,"review");XCTAssertEqual(r.next,"none")
        XCTAssertNotEqual(r.reply?.state,"completed")
    }
    func testWrongOperationAndChangedDayCannotReplaceReceipt() async throws {
        for mutation in ["operation","day"] {
            let (f,w,_,c)=try await setup();let image=f.image
            let corrupt=try f.changed { $0[mutation] = mutation == "operation" ? "bad" : f.completed.day+1 as Any }
            await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:corrupt)])
            let r=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(r.note,"review")
            XCTAssertEqual(r.day,f.prepare.day)
        }
    }
    func testValidHashCannotHideOutOfImagePolygon() async throws {
        let (f,w,_,c)=try await setup();let image=f.image
        let bad=try f.changed { dict in
            var result=dict["result"] as! [String:Any];result["wound_polygons"]=[[[0,0],[99999,0],[0,10]]]
            let raw=String(data:try! JSONSerialization.data(withJSONObject:result,options:.sortedKeys),encoding:.utf8)!
            var receipt=dict["receipt"] as! [String:Any];receipt["result_sha256"]=LiteServiceBytes.hash(Data(raw.utf8))
            dict["result"]=result;dict["result_json"]=raw;dict["receipt"]=receipt
        }
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:bad)])
        let r=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(r.note,"review")
    }
    func testQuotaWaitIsBoundedAndDoesNotSendMedia() async throws {
        let (f,w,_,c)=try await setup()
        await w.plan([ServiceStep(action:"prepare",data:error("lite_usage_limit"),code:429,wait:"999999999")])
        let r=try await c.resume(f.capture_id) { _ in throw LiteServiceFailure.missingImage }
        XCTAssertEqual(r.note,"quota");XCTAssertEqual(r.retryAt,4600);XCTAssertEqual(r.next,"prepare")
        XCTAssertTrue(r.message.contains("額度"));let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testAttemptLimitAndReservedOrUsedQuotaKeepThePhotoAndOriginalOperation() async throws {
        for reason in ["attempt_quota", "success_quota"] {
            let (fixture, wire, _, service) = try await setup()
            let body = try JSONSerialization.data(withJSONObject: ["error": "lite_usage_limit", "reason": reason])
            await wire.plan([ServiceStep(action: "prepare", data: body, code: 429, wait: "30")])
            let result = try await service.resume(fixture.capture_id) { _ in XCTFail("Asked for photo"); return nil }
            let saved = try await service.stored(fixture.capture_id)
            XCTAssertEqual(saved.operation, result.operation)
            XCTAssertEqual(saved.note, "quota")
            XCTAssertEqual(saved.attemptLimitReached == true, reason == "attempt_quota")
            XCTAssertEqual(saved.message.contains("嘗試已達上限"), reason == "attempt_quota")
            let requests = await wire.requests()
            XCTAssertEqual(requests.count, 1)
        }
    }

    func testCapacityWaitIsDistinctAndMalformedWaitDoesNotLockForever() async throws {
        let (f,w,_,c)=try await setup()
        await w.plan([ServiceStep(action:"prepare",data:error("lite_service_budget"),code:429,wait:"inf")])
        let r=try await c.resume(f.capture_id) { _ in nil }
        XCTAssertEqual(r.note,"capacity");XCTAssertEqual(r.retryAt,1005)
    }

    func testQuotaReasonSurvivesRelaunchAndSamePhotoResumesOnce() async throws {
        for state in ["pending", "used", "unknown"] {
            let (f, wire, journal, service) = try await setup()
            let body = try JSONSerialization.data(withJSONObject: [
                "error": "lite_usage_limit", "reason": "success_quota", "success_quota_state": state
            ])
            await wire.plan([ServiceStep(action: "prepare", data: body, code: 429, wait: "30")])
            let blocked = try await service.resume(f.capture_id) { _ in XCTFail("Read image before admission"); return nil }
            let early = try client(wire, journal, 1029)
            let saved = try await early.resume(f.capture_id) { _ in XCTFail(); return nil }
            XCTAssertEqual(saved, blocked)
            XCTAssertEqual(saved.successQuotaState?.rawValue, state == "unknown" ? nil : state)
            let expected = ["pending": "暫時占用", "used": "已用完", "unknown": "等候可用額度"]
            XCTAssertTrue(saved.message.contains(try XCTUnwrap(expected[state])))
            let ready = try client(wire, journal, 1030)
            let jpeg = f.image
            await wire.plan([ServiceStep(action: "prepare", data: try f.bytes(f.prepare)),
                             ServiceStep(action: "run", data: try f.bytes(f.completed))])
            let done = try await ready.resume(f.capture_id) { _ in jpeg }
            XCTAssertEqual(done.operation, blocked.operation)
            XCTAssertEqual(done.note, "confirmed")
            let sent = await wire.requests()
            XCTAssertEqual(sent.map { $0.url?.lastPathComponent }, ["prepare", "prepare", "run"])
        }
    }
    func testBusyRunKeepsOriginalWorkAndQueriesStatusBeforeResendingPhoto() async throws {
        let (fixture, wire, journal, service) = try await setup()
        let jpeg = fixture.image
        await wire.plan([
            ServiceStep(action: "prepare", data: try fixture.bytes(fixture.prepare)),
            ServiceStep(action: "run", data: error("lite_model_busy"), code: 429, wait: "5")
        ])
        let waiting = try await service.resume(fixture.capture_id) { _ in jpeg }
        XCTAssertEqual(waiting.note, "capacity")
        XCTAssertTrue(waiting.message.contains("不需重新拍攝"))
        XCTAssertEqual(waiting.next, "status")
        let beforeDue = try await client(wire, journal, 1004).resume(fixture.capture_id) { _ in
            XCTFail("照片在等待期間不應重送"); return nil
        }
        XCTAssertEqual(beforeDue, waiting)
        await wire.plan([
            ServiceStep(action: "status", data: try fixture.bytes(fixture.prepare)),
            ServiceStep(action: "run", data: try fixture.bytes(fixture.completed))
        ])
        let done = try await client(wire, journal, 1005).resume(fixture.capture_id) { _ in jpeg }
        XCTAssertEqual(done.note, "confirmed")
        XCTAssertEqual(done.operation, waiting.operation)
        XCTAssertEqual(done.imageSHA256, waiting.imageSHA256)
        let requests = await wire.requests()
        XCTAssertEqual(requests.map { $0.url?.lastPathComponent }, ["prepare","run","status","run"])
        XCTAssertNil(requests[2].httpBody?.range(of: jpeg))
    }
    func testOldSavedDeadlineIsShortenedWithoutSending() async throws {
        let (f,w,m,c)=try await setup()
        var saved=try await c.stored(f.capture_id);saved.retryAt=999999999
        await m.corrupt(f.capture_id,try JSONEncoder().encode(saved))
        let r=try await c.resume(f.capture_id) { _ in nil };XCTAssertEqual(r.retryAt,4600)
        let sent=await w.requests();XCTAssertTrue(sent.isEmpty)
    }
    func testUnknownAfterPinnedReceiptCannotReprepare() async throws {
        let (f,w,m,c)=try await setup();let image=f.image
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:Data(),networkError:.timedOut)])
        _=try await c.resume(f.capture_id) { _ in image }
        await w.plan([ServiceStep(action:"status",data:error("lite_operation_unknown"),code:404)])
        let r=try await client(w,m,1006).resume(f.capture_id) { _ in nil }
        XCTAssertEqual(r.note,"review");XCTAssertEqual(r.next,"none")
        let sent=await w.requests();XCTAssertEqual(sent.count,3)
    }
    func testGrantOrExpiryCannotChangeAfterReservation() async throws {
        for field in ["grant_sha256","admitted_limit","result_expires"] {
            let (f,w,_,c)=try await setup();let image=f.image
            let altered=try f.changed { dict in
                if field == "result_expires" { dict[field]=f.completed.result_expires!+1 }
                else {
                    var receipt=dict["receipt"] as! [String:Any]
                    receipt[field]=field == "admitted_limit" ? 10 : String(repeating:"b",count:64) as Any
                    dict["receipt"]=receipt
                }
            }
            await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare)),ServiceStep(action:"run",data:altered)])
            let r=try await c.resume(f.capture_id) { _ in image };XCTAssertEqual(r.note,"review")
        }
    }
    func testAuthenticationRejectionKeepsOperationForStatusInsteadOfNewIdentity() async throws {
        let (f,w,_,c)=try await setup()
        await w.plan([ServiceStep(action:"prepare",data:error("lite_attestation_required"),code:401)])
        let r=try await c.resume(f.capture_id) { _ in nil }
        XCTAssertEqual(r.note,"unavailable");XCTAssertEqual(r.next,"status");XCTAssertEqual(r.operation,f.capture_id)
    }
    func testExpiredResultStopsAndPreservesLocalJournal() async throws {
        let (f,w,m,c)=try await setup()
        await w.plan([ServiceStep(action:"prepare",data:error("lite_operation_expired"),code:410)])
        let r=try await c.resume(f.capture_id) { _ in nil };XCTAssertEqual(r.note,"expired")
        let bytes=await m.load(f.capture_id);XCTAssertNotNil(bytes)
        _=try await c.resume(f.capture_id) { _ in nil };let sent=await w.requests();XCTAssertEqual(sent.count,1)
    }
    func testDiskFailureBeforeDispatchSendsNothing() async throws {
        let (f,w,m,c)=try await setup();await m.fail(2)
        do { _=try await c.resume(f.capture_id) { _ in nil };XCTFail() } catch { }
        let sent=await w.requests();XCTAssertTrue(sent.isEmpty)
        let r=try await c.stored(f.capture_id);XCTAssertEqual(r.next,"prepare")
    }
    func testDiskFailureAfterSuccessRecoversByStatus() async throws {
        let (f,w,m,c)=try await setup();let image=f.image;try await w.plan(doneSteps(f));await m.fail(5)
        do { _=try await c.resume(f.capture_id) { _ in image };XCTFail() } catch { }
        let saved=try await c.stored(f.capture_id);XCTAssertEqual(saved.next,"status")
        await w.plan([ServiceStep(action:"status",data:try f.bytes(f.completed))])
        let r=try await client(w,m).resume(f.capture_id) { _ in throw LiteServiceFailure.missingImage }
        XCTAssertEqual(r.note,"confirmed");let sent=await w.requests();XCTAssertEqual(sent.count,3)
    }
    func testCorruptJournalIsNotReplacedOrUploaded() async throws {
        let (f,w,m,c)=try await setup();let damaged=Data("broken".utf8);await m.corrupt(f.capture_id,damaged)
        do { _=try await c.resume(f.capture_id) { _ in nil };XCTFail() } catch { }
        let stored=await m.load(f.capture_id);XCTAssertEqual(stored,damaged)
        let sent=await w.requests();XCTAssertTrue(sent.isEmpty)
    }
    func testOwnerChangeStopsWithoutNewUpload() async throws {
        let (f,w,_,c)=try await setup();await w.changeOwner()
        do { _=try await c.resume(f.capture_id) { _ in nil };XCTFail() } catch { }
        let sent=await w.requests();XCTAssertTrue(sent.isEmpty)
    }
    func testConcurrentResumeDoesNotDuplicateRequests() async throws {
        let (f,w,_,c)=try await setup();let image=f.image
        await w.plan([ServiceStep(action:"prepare",data:try f.bytes(f.prepare),delay:true),ServiceStep(action:"run",data:try f.bytes(f.completed))])
        let first=Task { try await c.resume(f.capture_id) { _ in image } }
        for _ in 0..<50 { if !(await w.requests()).isEmpty { break };try await Task.sleep(nanoseconds:2_000_000) }
        do { _=try await c.resume(f.capture_id) { _ in image };XCTFail() } catch { }
        let result=try await first.value;XCTAssertEqual(result.note,"confirmed")
        let sent=await w.requests();XCTAssertEqual(sent.count,2)
    }
    func testProtectedFileJournalCASRestartAndSymlinkGuard() async throws {
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        let f=try ServiceFixture.load(),w=ServiceWire(owner:f.installation),j=LiteServiceFileJournal(directory:directory)
        let c=try client(w,j);let r=try await c.enqueue(captureID:f.capture_id,jpeg:f.image)
        let other=LiteServiceFileJournal(directory:directory)
        let restored=try await client(w,other).stored(f.capture_id);XCTAssertEqual(restored,r)
        let path=directory.appendingPathComponent(f.capture_id+".json")
        XCTAssertTrue(try path.resourceValues(forKeys:[.isExcludedFromBackupKey]).isExcludedFromBackup == true)
        do { try await other.replace(f.capture_id,expected:nil,with:Data());XCTFail() } catch { }
        let original=try Data(contentsOf:path);let symlink=directory.appendingPathComponent(String(repeating:"a",count:32)+".json")
        try FileManager.default.createSymbolicLink(at:symlink,withDestinationURL:path)
        do { _=try await other.load(String(repeating:"a",count:32));XCTFail() } catch { }
        XCTAssertEqual(try Data(contentsOf:path),original)
    }
    func testNativeServiceRequestBindingsMatchPythonExactBytes() throws {
        let f=try ServiceFixture.load();XCTAssertEqual(f.signature_bindings.count,3)
        for row in f.signature_bindings {
            let data=try LiteAttestRequest.clientData(audience:row["audience"]!,installation:row["installation"]!,
                keyID:Data(base64Encoded:row["key_id"]!)!,challenge:Data(base64Encoded:row["challenge"]!)!,
                requestID:row["request_id"]!,method:row["method"]!,path:row["path"]!,contentType:row["content_type"]!,
                body:Data(base64Encoded:row["body"]!)!)
            XCTAssertEqual(data.base64EncodedString(),row["expected_client_data"])
            XCTAssertEqual(LiteServiceBytes.hash(data),row["expected_sha256"])
        }
    }
    func testServiceSignerUsesExistingIdentityAndSeparatePermission() async throws {
        let device=AttestDeviceDouble(),memory=AttestMemory(),http=AttestHTTPDouble(),permission=AttestPermission()
        let signer=try LiteAttestClient(origin:origin,audience:"lite-test",device:device,persistence:memory,transport:http,
            now:{Date(timeIntervalSince1970:1000)})
        let transport=LiteSignedServiceTransport(client:signer,allowService:{await permission.read()})
        do { _=try await transport.installation();XCTFail() } catch { }
        let emptyCounts=await device.counts();XCTAssertEqual(emptyCounts.0,0)
        let owner=try await signer.registeredInstallation()
        var request=URLRequest(url:origin.appendingPathComponent("api/v1/lite/service/status"));request.httpMethod="POST"
        request.setValue("application/json",forHTTPHeaderField:"Content-Type");request.httpBody=Data("{}".utf8)
        _=try await transport.send(request,installation:owner)
        let sent=await http.snapshot();XCTAssertNotNil(sent.1.first?.value(forHTTPHeaderField:"X-Lite-Assertion"))
        await permission.deny()
        do { _=try await transport.send(request,installation:owner);XCTFail() } catch { }
        let after=await http.snapshot();XCTAssertEqual(after.1.count,1)
        request.url=origin.appendingPathComponent("api/v1/lite/segment")
        do { _=try await transport.send(request,installation:owner);XCTFail() } catch { }
    }
}

extension LiteServiceOperationTests {
    func testOfflineRunSurvivesRestartAndOnlyQueriesTheOriginalResult() async throws {
        let (fixture, wire, journal, service) = try await setup()
        let id = fixture.capture_id
        let jpeg = fixture.image
        await wire.plan([
            ServiceStep(action: "prepare", data: try fixture.bytes(fixture.prepare)),
            ServiceStep(action: "run", data: Data(), networkError: .notConnectedToInternet)
        ])
        let interruption = try await service.resume(id) { _ in jpeg }
        XCTAssertEqual(interruption.note, "unavailable")
        XCTAssertTrue(interruption.message.contains("裝置離線"))
        XCTAssertEqual(interruption.next, "status")
        let reopened = try await client(wire, journal, 1005).storedIfPresent(id)
        XCTAssertEqual(reopened, interruption)
        await wire.plan([ServiceStep(action: "status", data: Data(), networkError: .timedOut)])
        let timedOut = try await client(wire, journal, 1005).resume(id, statusOnly: true) { _ in
            XCTFail("A status check must not load the photo"); return nil
        }
        XCTAssertFalse(timedOut.message.contains("離線"))
        XCTAssertEqual(timedOut.operation, id)
        await wire.plan([ServiceStep(action: "status", data: try fixture.bytes(fixture.completed))])
        let recovered = try await client(wire, journal, 1010).resume(id, statusOnly: true) { _ in
            XCTFail("A status check must not load the photo"); return nil
        }
        XCTAssertEqual(recovered.reply, fixture.completed)
        let requests = await wire.requests()
        XCTAssertEqual(requests.compactMap { $0.url?.lastPathComponent }, ["prepare", "run", "status", "status"])
    }

    func testGatewayLossKeepsTheOriginalOperationAndWaitsForStatus() async throws {
        for action in ["prepare", "run"] {
            for code in [404, 502, 503, 504] {
                let (fixture, wire, journal, service) = try await setup()
                let id = fixture.capture_id
                let jpeg = fixture.image
                let html = Data("<html>gateway unavailable</html>".utf8)
                let failure = ServiceStep(action: action, data: html, code: code, wait: "7")
                let ready = ServiceStep(action: "prepare", data: try fixture.bytes(fixture.prepare))
                await wire.plan(action == "run" ? [ready, failure] : [failure])
                let interrupted = try await service.resume(id) { _ in jpeg }
                let label = "\(action) HTTP \(code)"
                XCTAssertEqual(interrupted.next, "status", label)
                guard interrupted.next == "status" else { continue }
                XCTAssertEqual(interrupted.note, "unavailable", label)
                XCTAssertEqual(interrupted.retryAt, 1007, label)
                XCTAssertEqual(interrupted.operation, id, label)
                XCTAssertEqual(interrupted.imageSHA256, fixture.image_sha256, label)
                XCTAssertEqual(interrupted.requestDigest, action == "run" ? fixture.prepare.receipt?.request_digest : nil, label)
                let stored = try await client(wire, journal).stored(id)
                XCTAssertEqual(stored, interrupted, label)

                let before = await wire.requests()
                _ = try await client(wire, journal, 1006).resume(id) { _ in XCTFail(label); return nil }
                let beforeDeadline = await wire.requests()
                XCTAssertEqual(beforeDeadline.count, before.count, label)
                // A second outage during the status query only extends the wait.
                await wire.plan([ServiceStep(action: "status", data: html, code: code)])
                let waiting = try await client(wire, journal, 1007).resume(id) { _ in XCTFail(label); return nil }
                XCTAssertEqual(waiting.next, "status", label)
                XCTAssertEqual(waiting.retryAt, 1012, label)
                let done = ServiceStep(action: "status", data: try fixture.bytes(fixture.completed))
                let recovered: LiteServiceRecord
                if action == "prepare" {
                    // No photo was sent earlier. Only an authoritative ready reply authorizes the first run.
                    await wire.plan([ServiceStep(action: "status", data: ready.data),
                                     ServiceStep(action: "run", data: done.data)])
                    recovered = try await client(wire, journal, 1012).resume(id) { _ in jpeg }
                } else {
                    // The original run reached the server and completed despite the gateway failure.
                    await wire.plan([done])
                    recovered = try await client(wire, journal, 1012).resume(id) { _ in XCTFail(label); return nil }
                }
                XCTAssertEqual(recovered.note, "confirmed", label)
                XCTAssertEqual(recovered.reply, fixture.completed, label)
                XCTAssertEqual(recovered.operation, id, label)
                XCTAssertNil(recovered.recoveryHistory, label)
                let requests = await wire.requests()
                let actions = requests.compactMap { $0.url?.lastPathComponent }
                XCTAssertEqual(actions, action == "prepare" ? ["prepare", "status", "status", "run"] :
                                                          ["prepare", "run", "status", "status"], label)
                for request in requests {
                    let body = try XCTUnwrap(request.httpBody)
                    if request.url?.lastPathComponent == "run" {
                        XCTAssertNotNil(body.range(of: jpeg), label)
                        XCTAssertNotNil(body.range(of: Data("\r\n\r\n\(id)\r\n".utf8)), label)
                    } else {
                        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
                        XCTAssertEqual(payload["operation"], id, label)
                        XCTAssertNil(body.range(of: jpeg), label)
                    }
                }
            }
        }
    }

    func testGatewayFormatsPreserveStructuredOrUnrecognizableFailuresForReview() async throws {
        for (body, type, expected) in [("", nil, "unavailable"), ("Not Found", nil, "unavailable"),
                                       ("Bad Gateway", nil, "unavailable"), ("Gateway Timeout", nil, "unavailable"),
                                       ("<!DOCTYPE HTML><html>Error</html>", "text/html", "unavailable"),
                                       ("upstream connection timeout", "text/plain", "unavailable"),
                                       ("{broken", "text/plain", "review"), ("{}", "text/html", "review"),
                                       ("<html>error</html>", "application/json", "review"),
                                       ("unrecognized response", nil, "review")] as [(String, String?, String)] {
            let (fixture, wire, _, service) = try await setup()
            await wire.plan([ServiceStep(action: "prepare", data: Data(body.utf8), code: 404, contentType: type)])
            let saved = try await service.resume(fixture.capture_id) { _ in XCTFail(); return nil }
            XCTAssertEqual(saved.note, expected, body)
            XCTAssertEqual(saved.next, expected == "review" ? "none" : "status", body)
        }
    }

    func testApplicationErrorsAndInvalidReceiptsStillRequireReview() async throws {
        let rejected = Data("<html>unavailable</html>".utf8)
        for (code, data, note) in [(404, error("other_business_error"), "review"),
                                   (502, error("other_business_error"), "review"),
                                   (504, Data("{broken receipt".utf8), "review"),
                                   (410, rejected, "expired"), (422, rejected, "rejected"),
                                   (200, rejected, "review")] {
            let (fixture, wire, journal, service) = try await setup()
            await wire.plan([ServiceStep(action: "prepare", data: data, code: code)])
            let saved = try await service.resume(fixture.capture_id) { _ in XCTFail(); return nil }
            XCTAssertEqual(saved.note, note)
            XCTAssertEqual(saved.next, "none")
            let after = try await client(wire, journal, 9000).resume(fixture.capture_id) { _ in XCTFail(); return nil }
            XCTAssertEqual(after, saved)
            let requests = await wire.requests()
            XCTAssertEqual(requests.count, 1)
        }
    }
}

extension LiteServiceOperationTests {
    func testReadOnlyUnknownReturnsPrepareForExplicitSameOperation() async throws {
        let (f, wire, journal, service) = try await setup()
        await wire.plan([ServiceStep(action: "prepare", data: Data(), networkError: .timedOut)])
        _ = try await service.resume(f.capture_id) { _ in XCTFail(); return nil }
        await wire.plan([ServiceStep(action: "status", data: error("lite_operation_unknown"), code: 404)])
        let resumed = try client(wire, journal, 1006)
        let waiting = try await resumed.resume(f.capture_id, statusOnly: true) { _ in XCTFail(); return nil }
        XCTAssertEqual(waiting.next, "prepare")
        _ = try await resumed.resume(f.capture_id, statusOnly: true) { _ in XCTFail(); return nil }
        let queried = await wire.requests()
        XCTAssertEqual(queried.compactMap { $0.url?.lastPathComponent }, ["prepare", "status"])
        try await wire.plan(doneSteps(f))
        let done = try await resumed.resume(f.capture_id) { _ in f.image }
        XCTAssertEqual(done.note, "confirmed"); XCTAssertEqual(done.operation, f.capture_id)
        let all = await wire.requests()
        XCTAssertEqual(all.filter { $0.url?.lastPathComponent == "run" }.count, 1)
    }

    func testReadOnlyCrossDayOfferLeavesRecoveryForExplicitContinuation() async throws {
        let (f, wire, journal, service) = try await recoverySetup()
        await wire.plan([ServiceStep(action: "prepare", data: Data(), networkError: .timedOut)])
        _ = try await service.resume(f.capture_id) { _ in XCTFail(); return nil }
        await wire.plan([ServiceStep(action: "status", data: try f.bytes(f.available))])
        let restarted = try client(wire, journal, 1006)
        let offered = try await restarted.resume(f.capture_id, statusOnly: true) { _ in XCTFail(); return nil }
        XCTAssertEqual(offered.next, "recover"); XCTAssertNil(offered.recoveryHistory)
        XCTAssertEqual(offered.operation, f.capture_id)
        await wire.plan([ServiceStep(action: "recover", data: try f.bytes(f.authorization))] + (try recoveryFinish(f)))
        let done = try await restarted.resume(f.capture_id) { _ in f.image }
        XCTAssertEqual(done.note, "confirmed"); XCTAssertEqual(done.recoveryHistory?.count, 1)
        let requests = await wire.requests()
        XCTAssertEqual(requests.compactMap { $0.url?.lastPathComponent }, ["prepare","status","recover","prepare","run"])
    }
}
