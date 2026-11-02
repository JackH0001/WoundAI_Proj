import XCTest
@testable import WoundLite

final class LitePrivacyResourceTests: XCTestCase {
    func testBothLanguagesAndStudyScopeAreAvailableInTheAppBundle() throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "woundlite", withExtension: "html"))
        let html = try String(contentsOf: url, encoding: .utf8)
        for text in ["WoundAILite 民眾版隱私權政策草稿", "WoundAILite Privacy Policy", "default-src 'none'",
                     "研究並保留服務帳號", "account deletion", "timeline PDF", "mailto:"] where !html.contains(text) {
            XCTFail("Missing disclosure: " + text)
        }
    }
}
