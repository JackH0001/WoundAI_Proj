import Foundation
import CryptoKit

/// Exact clientData for App Attest assertions. Does not generate a key, transmit,
/// authorize, or fall back to anonymous requests. Backend must verify the assertion
/// and consume its challenge/counter atomically before using the bound request.
enum LiteAttestRequest {
    enum Failure: Error { case invalidBinding }
    static let maxBodyBytes = 32 * 1024 * 1024

    static func clientData(audience: String, installation: String, keyID: Data,
                           challenge: Data, requestID: String, method: String,
                           path: String, contentType: String, body: Data) throws -> Data {
        func matches(_ value: String, _ pattern: String) -> Bool {
            // Whole UTF-16 range, so a trailing newline cannot pass a $ anchor.
            guard let regex = try? NSRegularExpression(pattern: pattern) else { return false }
            let range = NSRange(value.startIndex..., in: value)
            return regex.firstMatch(in: value, range: range)?.range == range
        }
        guard matches(audience, "[a-z0-9-]{1,64}"),
              matches(installation, "[A-Za-z0-9_-]{1,128}"),
              matches(requestID, "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
              contentType.utf8.count <= 200,
              contentType.utf8.allSatisfy({ (32...126).contains($0) }),
              keyID.count == 32, challenge.count == 32, body.count <= maxBodyBytes
        else { throw Failure.invalidBinding }
        let postPaths = ["/api/v1/lite/segment", "/api/v1/lite/annotation", "/api/v1/lite/annotation/revision"]
        guard (method == "POST" && postPaths.contains(path)) ||
              (method == "DELETE" && path == "/api/v1/lite/data/" + installation && body.isEmpty)
        else { throw Failure.invalidBinding }
        let fields = [Data(audience.utf8), Data(installation.utf8), keyID, challenge,
                      Data(requestID.utf8), Data(method.utf8), Data(path.utf8),
                      Data(contentType.utf8), Data(SHA256.hash(data: body))]
        var result = Data("woundlite.appattest.request/1\0".utf8)
        for field in fields {
            var size = UInt32(field.count).bigEndian
            withUnsafeBytes(of: &size) { result.append(contentsOf: $0) }
            result.append(field)
        }
        return result
    }
}
