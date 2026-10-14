import Foundation

/// Explicit, fixed demo destination. This adapter never obtains a Google token.
/// The service identity is checked before transmitting an application credential.
actor SyntheticReviewTransport: InstitutionAuthenticatedTransport {
    static let origin = "https://woundai-mmh-review-demo-421209514056.asia-east1.run.app"
    private let session: URLSession
    private let redirectGuard = InstitutionNoRedirect()
    private var active = true
    private var verified = false

    init(session: URLSession? = nil) {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.urlCredentialStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForRequest = 90
        config.timeoutIntervalForResource = 120
        self.session = session ?? URLSession(configuration: config)
    }

    func invalidate() { active = false; verified = false }

    private func validate(_ request: URLRequest) throws {
        guard active else { throw CancellationError() }
        guard let url = request.url, let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.scheme == "https", c.host == URL(string: Self.origin)?.host,
              c.port == nil, c.user == nil, c.password == nil, c.fragment == nil,
              c.path.hasPrefix("/api/"), !c.percentEncodedPath.contains("%"),
              !c.path.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else {
            throw InstitutionEntryError.invalidDestination
        }
    }

    private func transmit(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try validate(request)
        try Task.checkCancellation()
        var clean = request
        for header in ["Proxy-Authorization", "X-Serverless-Authorization", "Cookie"] {
            clean.setValue(nil, forHTTPHeaderField: header)
        }
        let (data, response) = try await session.data(for: clean, delegate: redirectGuard)
        try Task.checkCancellation()
        guard active else { throw CancellationError() }
        guard let response = response as? HTTPURLResponse, response.url == clean.url else {
            throw InstitutionEntryError.badResponse
        }
        guard !(300..<400).contains(response.statusCode) else { throw InstitutionEntryError.redirected }
        return (data, response)
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        try validate(request)
        if !verified {
            let (data, response) = try await transmit(URLRequest(url: URL(string: Self.origin + "/api/health")!))
            try InstitutionEntryHealth.validate(data, response: response, service: "woundai-mmh-review-demo")
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  object["deployment_environment"] as? String == "synthetic-review" else {
                throw InstitutionEntryError.badResponse
            }
            verified = true
        }
        // No write retries. The caller must validate the normal WoundAI receipt.
        return try await transmit(request)
    }
}
