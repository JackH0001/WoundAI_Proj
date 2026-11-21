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

    func testStorageLocationAndRetentionAreScopedInEachLanguage() throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "woundlite", withExtension: "html"))
        let html = try String(contentsOf: url, encoding: .utf8)

        for (language, expected) in [
            ("zh-Hant", ["台灣（asia-east1）", "全球（global）", "預設日誌桶的項目保留 30 天",
                         "必要稽核日誌桶的項目保留 400 天", "歷史副本與備份完成處理的最長天數仍待確認"]),
            ("en", ["Taiwan (asia-east1)", "log buckets as global",
                    "default log bucket are retained for 30 days", "required audit log bucket are retained for 400 days",
                    "maximum time to finish processing all historical copies and backups is still being verified"])
        ] {
            let start = try XCTUnwrap(html.range(of: "<section lang=\"\(language)\">")?.upperBound)
            let end = try XCTUnwrap(html.range(of: "</section>", range: start..<html.endIndex)?.lowerBound)
            let section = html[start..<end]
            for disclosure in expected {
                XCTAssertTrue(section.contains(disclosure), "Missing disclosure in \(language): \(disclosure)")
            }
        }
    }
}
