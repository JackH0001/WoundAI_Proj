import XCTest
@testable import WoundLite

final class LitePrivacyResourceTests: XCTestCase {
    func testBothLanguagesAndStudyScopeAreAvailableInTheAppBundle() throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "woundlite", withExtension: "html"))
        let html = try String(contentsOf: url, encoding: .utf8)
        for text in ["WoundAILite 民眾版隱私權政策草稿", "WoundAILite Privacy Policy", "default-src 'none'",
                     "研究並保留服務帳號", "account deletion", "timeline PDF", "mailto:",
                     "不保存搜尋紀錄", "does not save search history",
                     "紙上標靶", "paper targets", "拍攝回呼時間", "capture callback time",
                     "參加 TestFlight 測試與同意研究上傳是兩個獨立選擇", "Joining TestFlight and agreeing to research uploads are separate choices",
                     "請只用合成資料回報問題", "Use synthetic material when reporting issues"] where !html.contains(text) {
            XCTFail("Missing disclosure: " + text)
        }
    }
}
