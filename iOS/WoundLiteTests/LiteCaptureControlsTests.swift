import XCTest
import UIKit
import SwiftUI
@testable import WoundLite

@MainActor final class LiteCaptureControlsTests: XCTestCase {
    private let en = LiteCaptureControlsCopy(locale: Locale(identifier: "en_US"))
    private let zh = LiteCaptureControlsCopy(locale: Locale(identifier: "zh_Hant_TW"))
    func testBothLanguagesArePackagedAndPreserveErrorPlaceholder() throws {
        func table(_ language: String) throws -> [String:String] {
            let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteCaptureControls", withExtension: "strings", subdirectory: nil, localization: language))
            return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String:String])
        }
        for language in ["en", "zh-Hant"] {
            let path = try XCTUnwrap(Bundle.main.path(forResource: language, ofType: "lproj"))
            let bundle = try XCTUnwrap(Bundle(path: path))
            let notice = bundle.localizedString(forKey: "NSCameraUsageDescription", value: "MISSING", table: "InfoPlist")
            XCTAssertNotEqual(notice, "MISSING")
            XCTAssertEqual(notice, language == "en" ? "Take photos and LiDAR depth to outline and estimate area. Research upload requires separate consent." : "拍攝照片與 LiDAR 深度以圈選和估算面積。研究上傳須另行同意。")
        }
        let a = try table("en"), b = try table("zh-Hant")
        XCTAssertEqual(a.count, 30); XCTAssertEqual(Set(a.keys), Set(b.keys))
        for (key, value) in a {
            XCTAssertEqual(b[key], key); XCTAssertFalse(value.isEmpty)
            XCTAssertEqual(key.components(separatedBy: "%@").count, value.components(separatedBy: "%@").count)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }, key)
        }
    }
    func testSharedMedicalDefaultsAreUnchangedAndPresentationDoesNotCapture() {
        var calls = 0
        let camera = CameraCaptureView(onCapture: { _, _ in calls += 1 }, onCancel: { calls += 1 })
        for source in ["取消", "對焦中…", "相機權限未授權。請到 iOS 設定 → WoundAI 開啟相機權限。",
                       "📐 此裝置無 LiDAR 深度（照片照常可量測）"] {
            XCTAssertEqual(camera.displayText(source), source)
        }
        XCTAssertTrue(camera.hintText.contains("校正貼紙")); XCTAssertEqual(calls, 0)
        let image = UIImage()
        let editor = WoundEditView(image: image, initialPolygons: [], originalArea: nil,
            tissueFrac: [:], exudate: nil, mmPerPx: nil, resume: nil, wbGains: nil,
            onCancel: { calls += 1 }, onDone: { _, _, _, _, _, _ in calls += 1 })
        XCTAssertFalse(editor.boundaryOnly); XCTAssertEqual(editor.displayText("完成修邊"), "完成修邊")
        XCTAssertEqual(calls, 0)
    }
    func testLiteNeverClaimsCapturedDepthOrAreaFromDeviceSupportAlone() {
        for copy in [en, zh] {
            let ready = copy.camera("📐 LiDAR 深度：擷取中（隨照片加密保存，供 3D 重建研究）")
            XCTAssertEqual(ready, copy.text("LiDAR 可用；拍攝後會核對是否取得可用深度。"))
            let unavailable = copy.camera("📐 此裝置無 LiDAR 深度（照片照常可量測）")
            XCTAssertEqual(unavailable, copy.text("目前沒有可用 LiDAR 深度；本次無法提供免貼紙面積估算。"))
            XCTAssertFalse(ready.contains("研究")); XCTAssertFalse(ready.contains("research"))
        }
        XCTAssertTrue(en.camera("相機權限未授權。請到 iOS 設定 → WoundAI 開啟相機權限。").contains("WoundAILite"))
        let unavailable = en.camera("找不到可用相機（模擬器沒有相機，請改用「相簿」或「檔案」）。")
        XCTAssertTrue(unavailable.contains("Cancel keeps")); XCTAssertFalse(unavailable.contains("Files"))
    }
    func testUnknownFailureDetailsStayVerbatimWithoutFormatExpansion() {
        let detail = "AVError -11800: %@ %s\n原始詳細訊息"
        XCTAssertEqual(en.camera("拍攝失敗：" + detail), "Capture failed: " + detail)
        XCTAssertEqual(zh.camera("拍攝失敗：" + detail), "拍攝失敗：" + detail)
        XCTAssertEqual(en.camera("拍攝失敗：未知錯誤"), "Capture failed: Unknown error")
        XCTAssertEqual(en.camera("未知錯誤 XYZ %@"), "未知錯誤 XYZ %@")
    }
    func testEditorTranslationRendersWithoutInvokingCompletion() throws {
        let image = UIGraphicsImageRenderer(size: CGSize(width: 128, height: 128)).image { ctx in
            UIColor.orange.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 128, height: 128))
        }
        var completions = 0
        for copy in [en, zh] {
            let view = WoundEditView(image: image, initialPolygons: [], originalArea: nil,
                tissueFrac: [:], exudate: nil, mmPerPx: nil, resume: nil, wbGains: nil,
                boundaryOnly: true, displayText: copy.text,
                onCancel: { completions += 1 }, onDone: { _, _, _, _, _, _ in completions += 1 })
                .frame(width: 393, height: 760).background(Color.white).environment(\.colorScheme, .light)
            let renderer = ImageRenderer(content: view); renderer.scale = 2
            let rendered = try XCTUnwrap(renderer.uiImage)
            let attachment = XCTAttachment(image: rendered); attachment.name = "empty-editor-" + copy.locale.identifier
            attachment.lifetime = .keepAlways; add(attachment)
        }
        XCTAssertEqual(completions, 0)
    }
}
