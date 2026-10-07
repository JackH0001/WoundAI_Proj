import Foundation
import DeviceCheck
import Security
import CryptoKit

actor LiteAppleAttestDevice: LiteAttestDevice {
    func supported() -> Bool { DCAppAttestService.shared.isSupported }
    func generateKey() async throws -> String { try await DCAppAttestService.shared.generateKey() }
    func attest(key: String, hash: Data) async throws -> Data {
        do { return try await DCAppAttestService.shared.attestKey(key, clientDataHash: hash) }
        catch let error as DCError where error.code == .serverUnavailable { throw LiteAttestFailure.appleTemporarilyUnavailable }
    }
    func assertion(key: String, hash: Data) async throws -> Data {
        try await DCAppAttestService.shared.generateAssertion(key, clientDataHash: hash)
    }
}

/// Scope includes origin/audience and a per-install marker excluded from backup.
/// Stores the key identifier and temporary pending proof, never the private key.
actor LiteAttestKeychain: LiteAttestPersistence {
    private let account: String
    private let service = "com.woundai.lite.appattest.v1"
    init(scope: String) { account = scope }
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account, kSecAttrSynchronizable as String: false]
    }
    func load() throws -> Data? {
        var q = query; q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw LiteAttestFailure.unavailable }
        return data
    }
    func save(_ data: Data) throws {
        guard data.count <= 100_000 else { throw LiteAttestFailure.invalidState }
        let update = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly] as [String: Any]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var q = query; update.forEach { q[$0.key] = $0.value }
            guard SecItemAdd(q as CFDictionary, nil) == errSecSuccess else { throw LiteAttestFailure.unavailable }
        } else if status != errSecSuccess { throw LiteAttestFailure.unavailable }
    }
    func remove() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw LiteAttestFailure.unavailable }
    }
}
private final class LiteAttestNoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
final class LiteAttestURLTransport: LiteAttestTransport, @unchecked Sendable {
    private let session: URLSession
    private let redirects = LiteAttestNoRedirect()
    init() {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCredentialStorage = nil
        config.urlCache = nil; config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 45; config.timeoutIntervalForResource = 90
        session = URLSession(configuration: config)
    }
    deinit { session.invalidateAndCancel() }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (bytes, response) = try await session.bytes(for: request, delegate: redirects)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse else { throw LiteAttestFailure.invalidResponse }
        let limit = request.url?.path.contains("/attest/") == true ? 100_000 : 4 * 1024 * 1024
        if response.expectedContentLength > limit { throw LiteAttestFailure.invalidResponse }
        var data = Data()
        for try await byte in bytes {
            guard data.count < limit else { throw LiteAttestFailure.invalidResponse }
            data.append(byte)
        }
        return (data, http)
    }
}

/// Kept in the app container and excluded from backup. Keychain can outlive an
/// uninstall, so Keychain alone cannot determine whether an App Attest key lives.
enum LiteAttestInstallMarker {
    static func loadOrCreate(in directory: URL) throws -> String {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.complete])
        var file = directory.appendingPathComponent("app-attest-install-v1")
        let id: String
        if FileManager.default.fileExists(atPath: file.path) {
            let data = try Data(contentsOf: file)
            guard data.count == 32, let saved = String(data: data, encoding: .utf8),
                  LiteAttestClient.matches(saved, "[0-9a-f]{32}") else { throw LiteAttestFailure.invalidState }
            id = saved
        } else {
            id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            try Data(id.utf8).write(to: file, options: [.atomic, .completeFileProtection])
        }
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try file.setResourceValues(values)
        return id
    }
}

actor LiteAttestClients {
    static let shared = LiteAttestClients()
    private var clients: [String: LiteAttestClient] = [:]
    func client(origin: URL, audience: String) throws -> LiteAttestClient {
        guard var components = URLComponents(url: origin, resolvingAgainstBaseURL: false),
              components.scheme == "https", components.host != nil, components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil, components.percentEncodedPath == "" || components.percentEncodedPath == "/",
              LiteAttestClient.matches(audience, "[a-z0-9-]{1,64}") else { throw LiteAttestFailure.invalidConfiguration }
        components.host = components.host?.lowercased(); components.path = ""
        if components.port == 443 { components.port = nil }
        guard let origin = components.url else { throw LiteAttestFailure.invalidConfiguration }
        let binding = origin.absoluteString + "\0" + audience
        if let client = clients[binding] { return client }
        guard clients.count < 8 else { throw LiteAttestFailure.busy }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let installation = try LiteAttestInstallMarker.loadOrCreate(in: base.appendingPathComponent("app-attest", isDirectory: true))
        let scope = SHA256.hash(data: Data((binding + "\0" + installation).utf8)).map { String(format: "%02x", $0) }.joined()
        let client = try LiteAttestClient(origin: origin, audience: audience, device: LiteAppleAttestDevice(),
                                         persistence: LiteAttestKeychain(scope: scope), transport: LiteAttestURLTransport())
        clients[binding] = client
        return client
    }
}
