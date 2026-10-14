import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

private actor EntryTokenFixture: InstitutionIDTokenProvider {
    let token: InstitutionIDToken
    private(set) var calls = 0
    init(_ token: InstitutionIDToken) { self.token = token }
    func freshToken() async throws -> InstitutionIDToken { calls += 1; return token }
}

private actor WaitingEntryToken: InstitutionIDTokenProvider {
    private var waiter: CheckedContinuation<InstitutionIDToken, Error>?
    private var started: CheckedContinuation<Void, Never>?
    func freshToken() async throws -> InstitutionIDToken {
        try await withCheckedThrowingContinuation { waiter = $0; started?.resume(); started = nil }
    }
    func waitUntilStarted() async {
        if waiter != nil { return }
        await withCheckedContinuation { started = $0 }
    }
    func finish(_ token: InstitutionIDToken) { waiter?.resume(returning:token); waiter = nil }
}

private actor FirstTokenGate: InstitutionIDTokenProvider {
    private let token: InstitutionIDToken
    private var waiter: CheckedContinuation<Void, Never>?
    private(set) var calls = 0
    init(_ token: InstitutionIDToken) { self.token = token }
    func freshToken() async throws -> InstitutionIDToken {
        calls += 1
        if calls == 1 { await withCheckedContinuation { waiter = $0 } }
        return token
    }
    func release() { waiter?.resume(); waiter = nil }
}

private actor DelayedEntryReply: InstitutionAuthenticatedTransport {
    private var waiter: CheckedContinuation<(Data, HTTPURLResponse), Error>?
    private var started: CheckedContinuation<Void, Never>?
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try await withCheckedThrowingContinuation { waiter = $0; started?.resume(); started = nil }
    }
    func waitUntilStarted() async {
        if waiter != nil { return }
        await withCheckedContinuation { started = $0 }
    }
    func invalidate() {}
    func finish() {
        let data=Data(#"{"access_token":"stale-app-token","identity":"mmhps20261007:test","org":"mmhps20261007","user":"test","role":"nurse","perms":[]}"#.utf8)
        waiter?.resume(returning:(data,HTTPURLResponse(url:URL(string:"https://mmh-entry.invalid/api/auth/login")!,statusCode:200,httpVersion:nil,headerFields:nil)!));waiter=nil
    }
}

private final class EntryHTTPFixture: URLProtocol {
    static let lock = NSLock()
    private static var handler: ((URLRequest) -> (Int, Data))?
    static func use(_ value: ((URLRequest) -> (Int, Data))?) { lock.lock(); defer { lock.unlock() }; handler = value }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock(); let handle = Self.handler; Self.lock.unlock()
        guard let handle else { XCTFail("Unexpected HTTP request"); client?.urlProtocol(self,didFailWithError:URLError(.cancelled)); return }
        let (code,data) = handle(request)
        client?.urlProtocol(self,didReceive:HTTPURLResponse(url:request.url!,statusCode:code,httpVersion:nil,headerFields:nil)!,cacheStoragePolicy:.notAllowed)
        client?.urlProtocol(self,didLoad:data);client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class InstitutionIAPTransportTests: XCTestCase {
    private let origin = URL(string:"https://mmh-entry.invalid")!
    private let now = Date(timeIntervalSince1970:2_000_000_000)
    private func token(subject:String="google-person-1", audience:String="test-audience", lifetime:TimeInterval=3600,
                       value:String="synthetic.google.token") -> InstitutionIDToken {
        InstitutionIDToken(value:value,subject:subject,audience:audience,expiresAt:now.addingTimeInterval(lifetime))
    }
    private func transport(_ provider: any InstitutionIDTokenProvider) throws -> InstitutionIAPTransport {
        let config = URLSessionConfiguration.ephemeral;config.protocolClasses=[EntryHTTPFixture.self]
        let time = now
        return try InstitutionIAPTransport(origin:origin,audience:"test-audience",subject:"google-person-1",
            provider:provider,session:URLSession(configuration:config),clock:{ time })
    }
    override func tearDown() { EntryHTTPFixture.use(nil);super.tearDown() }
    private func rejected(_ operation: () async throws -> Void) async {
        do { try await operation(); XCTFail("Expected rejection") } catch {}
    }

    func testConcurrentClientsWaitForPreviousRequestAndCancelledWaiterDoesNotSend() async throws {
        let provider = FirstTokenGate(token()); let t = try transport(provider)
        let request = URLRequest(url: origin.appendingPathComponent("api/health"))
        let sent = expectation(description: "only active and surviving queued requests")
        sent.expectedFulfillmentCount = 2; sent.assertForOverFulfill = true
        EntryHTTPFixture.use { _ in sent.fulfill(); return (200, Data("{}".utf8)) }
        let first = Task { try await t.send(request) }
        for _ in 0..<100 { if await provider.calls > 0 { break }; try await Task.sleep(nanoseconds: 1_000_000) }
        let cancelled = Task { try await t.send(request) }
        let next = Task { try await t.send(request) }
        try await Task.sleep(nanoseconds: 50_000_000)
        let before = await provider.calls
        XCTAssertEqual(before, 1, "A second client must not start token/network work while the first is pending")
        cancelled.cancel()
        await rejected { _ = try await cancelled.value }
        await provider.release()
        _ = try await first.value; _ = try await next.value
        let after = await provider.calls; XCTAssertEqual(after, 2)
        await fulfillment(of: [sent], timeout: 2)
    }
    func testSignOutReleasesQueuedWaitersWithoutSendingThem() async throws {
        let provider = FirstTokenGate(token()); let t = try transport(provider)
        let request = URLRequest(url: origin.appendingPathComponent("api/health"))
        let first = Task { try await t.send(request) }
        for _ in 0..<100 { if await provider.calls > 0 { break }; try await Task.sleep(nanoseconds: 1_000_000) }
        let next = Task { try await t.send(request) }
        try await Task.sleep(nanoseconds: 50_000_000)
        await t.invalidate()
        await rejected { _ = try await next.value }
        await provider.release()
        await rejected { _ = try await first.value }
        let count = await provider.calls; XCTAssertEqual(count, 1)
    }

    func testTwoIdentitiesAreSeparateAndConflictingHeadersRemoved() async throws {
        let done=expectation(description:"request")
        EntryHTTPFixture.use { r in
            XCTAssertEqual(r.value(forHTTPHeaderField:"Proxy-Authorization"),"Bearer synthetic.google.token")
            XCTAssertEqual(r.value(forHTTPHeaderField:"Authorization"),"Bearer application-token")
            XCTAssertNil(r.value(forHTTPHeaderField:"X-Serverless-Authorization"))
            XCTAssertNil(r.value(forHTTPHeaderField:"Cookie"))
            XCTAssertEqual(r.httpMethod,"POST");done.fulfill();return (200,Data("{}".utf8))
        }
        let t=try transport(EntryTokenFixture(token()))
        var request=URLRequest(url:origin.appendingPathComponent("api/v1/depth"));request.httpMethod="POST"
        request.setValue("Bearer application-token",forHTTPHeaderField:"Authorization")
        request.setValue("wrong",forHTTPHeaderField:"Proxy-Authorization")
        request.setValue("wrong",forHTTPHeaderField:"X-Serverless-Authorization")
        request.setValue("browser-session",forHTTPHeaderField:"Cookie")
        _ = try await t.send(request);await fulfillment(of:[done],timeout:2)
    }
    func testWrongOriginAndUnsafeURLsRefusedBeforeObtainingToken() async throws {
        let provider=EntryTokenFixture(token());let t=try transport(provider)
        for url in ["https://other.invalid/api/health","http://mmh-entry.invalid/api/health",
                    "https://mmh-entry.invalid:443/api/health","https://user@mmh-entry.invalid/api/health",
                    "https://mmh-entry.invalid/api/health#fragment","https://mmh-entry.invalid/console",
                    "https://mmh-entry.invalid/api/%2e%2e/console"] {
            await rejected { _ = try await t.send(URLRequest(url:URL(string:url)!)) }
        }
        let count=await provider.calls;XCTAssertEqual(count,0)
    }
    func testInvalidConfigurationRejected() throws {
        for url in ["http://mmh-entry.invalid","https://user@mmh-entry.invalid","https://mmh-entry.invalid:443",
                    "https://mmh-entry.invalid/api","https://mmh-entry.invalid?secret=x"] {
            XCTAssertThrowsError(try InstitutionIAPTransport(origin:URL(string:url)!,audience:"test-audience",subject:"google-person-1",provider:EntryTokenFixture(token())))
        }
    }
    func testWrongSubjectAudienceExpiredAndHeaderInjectionRefusedWithoutHTTP() async throws {
        for bad in [token(subject:"another-person"), token(audience:"another-service"),token(lifetime:30),
                    token(lifetime:-1),token(value:""),token(value:"token\r\nInjected: true")] {
            let t=try transport(EntryTokenFixture(bad))
            await rejected { _ = try await t.send(URLRequest(url:origin.appendingPathComponent("api/health"))) }
        }
    }
    func testInvalidationBeforeSendDoesNotObtainToken() async throws {
        let provider=EntryTokenFixture(token());let t=try transport(provider)
        await t.invalidate()
        await rejected { _ = try await t.send(URLRequest(url:origin.appendingPathComponent("api/health"))) }
        let count=await provider.calls;XCTAssertEqual(count,0)
    }
    func testSignOutDuringRefreshCannotResumeOldRequest() async throws {
        let provider=WaitingEntryToken();let t=try transport(provider)
        let req=URLRequest(url:origin.appendingPathComponent("api/auth/login"))
        let pending=Task { try await t.send(req) }
        await provider.waitUntilStarted();await t.invalidate();await provider.finish(token())
        await rejected { _ = try await pending.value }
    }
    func testRedirectResponseRejectedWithoutRetry() async throws {
        let once=expectation(description:"one request");once.assertForOverFulfill=true
        EntryHTTPFixture.use { _ in once.fulfill();return (302,Data()) }
        let t=try transport(EntryTokenFixture(token()))
        await rejected { _ = try await t.send(URLRequest(url:origin.appendingPathComponent("api/health"))) }
        await fulfillment(of:[once],timeout:2)
    }
    func testURLSessionRedirectCallbackAlwaysCancels() {
        let session=URLSession(configuration:.ephemeral);defer { session.invalidateAndCancel() }
        let req=URLRequest(url:origin.appendingPathComponent("api/health"));let task=session.dataTask(with:req)
        let guardDelegate=InstitutionNoRedirect()
        for target in [origin,URL(string:"https://other.invalid")!] {
            guardDelegate.urlSession(session,task:task,willPerformHTTPRedirection:HTTPURLResponse(url:req.url!,statusCode:307,httpVersion:nil,headerFields:nil)!,newRequest:URLRequest(url:target)) { redirected in
                XCTAssertNil(redirected)
            }
        }
    }
    func testRBACDenialReturnedWithoutAutomaticWriteRetry() async throws {
        let once=expectation(description:"one write");once.assertForOverFulfill=true
        EntryHTTPFixture.use { _ in once.fulfill();return (403,Data("role-denied".utf8)) }
        let t=try transport(EntryTokenFixture(token()))
        var req=URLRequest(url:origin.appendingPathComponent("api/v1/annotation"));req.httpMethod="POST"
        let (data,response)=try await t.send(req)
        XCTAssertEqual(response.statusCode,403);XCTAssertEqual(String(decoding:data,as:UTF8.self),"role-denied")
        await fulfillment(of:[once],timeout:2)
    }
    func testActualAppLoginPreservesPasswordBodyAndLogoutRejectsFurtherRequests() async throws {
        let once=expectation(description:"login")
        EntryHTTPFixture.use { r in
            XCTAssertEqual(r.url?.path,"/api/auth/login")
            XCTAssertNil(r.value(forHTTPHeaderField:"Authorization"))
            XCTAssertNotNil(r.value(forHTTPHeaderField:"Proxy-Authorization"))
            var body=r.httpBody ?? Data()
            if let stream=r.httpBodyStream {
                stream.open();defer { stream.close() }
                var bytes=[UInt8](repeating:0,count:512)
                while stream.hasBytesAvailable {
                    let n=stream.read(&bytes,maxLength:bytes.count)
                    if n<=0 { break };body.append(contentsOf:bytes.prefix(n))
                }
            }
            let payload=(try? JSONSerialization.jsonObject(with:body)) as? [String:String]
            XCTAssertEqual(payload?["username"],"mmhps20261007:test")
            XCTAssertEqual(payload?["password"],"synthetic-password")
            once.fulfill()
            return (200,Data(#"{"access_token":"app-token","identity":"mmhps20261007:test","org":"mmhps20261007","user":"test","role":"nurse","perms":[]}"#.utf8))
        }
        let t=try transport(EntryTokenFixture(token()))
        let client=BackendClient(baseUrl:origin.absoluteString,institutionTransport:t)
        let ok=try await client.login(username:"mmhps20261007:test",password:"synthetic-password")
        XCTAssertTrue(ok)
        let identity=await client.currentIdentity();XCTAssertEqual(identity?.org,"mmhps20261007")
        await client.logout()
        let after=await client.currentIdentity();XCTAssertNil(after)
        let signedIn=await client.isLoggedIn;XCTAssertFalse(signedIn)
        await rejected { _ = try await client.login(username:"mmhps20261007:test",password:"synthetic-password") }
        await fulfillment(of:[once],timeout:2)
    }
    func testAppLogoutDiscardsLateSuccessfulLoginEvenIfTransportCompletes() async throws {
        let delayed=DelayedEntryReply()
        let client=BackendClient(baseUrl:origin.absoluteString,institutionTransport:delayed)
        let pending=Task { try await client.login(username:"test",password:"synthetic") }
        await delayed.waitUntilStarted()
        await client.logout()
        await delayed.finish()
        await rejected { _ = try await pending.value }
        let identity=await client.currentIdentity();XCTAssertNil(identity)
        let loggedIn=await client.isLoggedIn;XCTAssertFalse(loggedIn)
    }
    func testActualBackendHealthUsesEntryBeforeAppLogin() async throws {
        let once=expectation(description:"health")
        EntryHTTPFixture.use { r in
            XCTAssertEqual(r.url?.path,"/api/health")
            XCTAssertNotNil(r.value(forHTTPHeaderField:"Proxy-Authorization"))
            XCTAssertNil(r.value(forHTTPHeaderField:"Authorization"))
            once.fulfill();return (200,Data("{\"status\":\"healthy\"}".utf8))
        }
        let t=try transport(EntryTokenFixture(token()))
        let client=BackendClient(baseUrl:origin.absoluteString,institutionTransport:t)
        let health=try await client.health();XCTAssertEqual(health.status,"healthy")
        await client.logout()
        await rejected { _ = try await client.health() }
        await fulfillment(of:[once],timeout:2)
    }
}

final class InstitutionEntryHealthTests: XCTestCase {
    private func response(_ status: Int) -> HTTPURLResponse {
        HTTPURLResponse(url: URL(string:"https://mmh-entry.invalid/api/health")!, statusCode:status, httpVersion:nil, headerFields:nil)!
    }
    func testEntryAcceptsOnlySuccessfulExpectedService() throws {
        let data=Data(#"{"build":{"service":"expected-mmh","revision":"candidate"}}"#.utf8)
        XCTAssertNoThrow(try InstitutionEntryHealth.validate(data,response:response(200),service:"expected-mmh"))
        for code in [302,401,403,500] {
            XCTAssertThrowsError(try InstitutionEntryHealth.validate(data,response:response(code),service:"expected-mmh"))
        }
    }
    func testHTMLAndDifferentServiceCannotMarkEntryReady() {
        for body in ["<html>Google login</html>","{}",#"{"build":{"service":"other","revision":"candidate"}}"#,
                     #"{"build":{"service":"expected-mmh","revision":""}}"#] {
            XCTAssertThrowsError(try InstitutionEntryHealth.validate(Data(body.utf8),response:response(200),service:"expected-mmh"))
        }
    }
}
