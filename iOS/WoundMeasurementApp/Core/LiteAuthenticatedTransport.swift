import Foundation

/// Separate from the medical JWT session. Only the Lite target supplies a live adapter.
protocol LiteAuthenticatedTransport: Sendable {
    func installation() async throws -> String
    func send(_ request: URLRequest, installation: String) async throws -> (Data, HTTPURLResponse)
}
