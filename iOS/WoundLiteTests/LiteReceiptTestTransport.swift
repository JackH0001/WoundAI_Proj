import Foundation
@testable import WoundLite

/// Parsing-only tests explicitly inject their HTTP fixture; this never exists in the app target.
struct LiteReceiptTestTransport: LiteAuthenticatedTransport {
    let session: URLSession
    func installation() -> String { "fixture" }
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        return (data, response as! HTTPURLResponse)
    }
}
