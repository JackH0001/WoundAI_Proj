import XCTest
import UIKit
import SwiftUI
@testable import WoundLite

@MainActor final class LiteCaptureCopyTests: XCTestCase {
    private let en = LiteCaptureCopy(locale: Locale(identifier: "en_US"))
    private let zh = LiteCaptureCopy(locale: Locale(identifier: "zh_Hant_TW"))
    private func result() -> DepthAreaResult {
        DepthAreaResult(surfaceAreaCm2: 10.08, projectedAreaCm2: 9.91,
            volumeMl: nil, maxDepthMm: nil, coverage: 0.95, medianDistanceM: 0.3, tiltDeg: nil)
    }
    private func photo() -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: 16, height: 16)).image { ctx in
            UIColor.orange.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 16, height: 16))
        }
    }
    func testPairedResourcesPreservePlaceholdersAndChinese() throws {
        func table(_ language: String) throws -> [String:String] {
            let url = try XCTUnwrap(Bundle.main.url(forResource: "LiteCapture", withExtension: "strings", subdirectory: nil, localization: language))
            return try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil) as? [String:String])
        }
        let a = try table("en"), b = try table("zh-Hant")
        XCTAssertEqual(a.count, 126); XCTAssertEqual(Set(a.keys), Set(b.keys))
        for (key, value) in a {
            XCTAssertEqual(b[key],key); XCTAssertFalse(value.isEmpty)
            XCTAssertEqual(key.components(separatedBy: "%@").count,value.components(separatedBy: "%@").count)
            XCTAssertFalse(value.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) },key)
        }
    }
    func testConfirmationPreservesActualNameLocationAndSelection() throws {
        let location = LiteWoundLocation(side: "right", site: "heel")
        let group = LiteWoundGroup(id: "original-id",name: "新增傷口 %@\n原名",location: location)
        let target = try XCTUnwrap(LiteWoundSaveTarget.resolve(addingNew:false,selectedID:group.id,entered:.init(side:"left",site:"heel"),groups:[group]))
        let before = target
        XCTAssertTrue(en.confirmation(target).contains(group.name))
        XCTAssertTrue(en.confirmation(target).contains("Right, Heel"))
        XCTAssertEqual(zh.confirmation(target),target.confirmationMessage)
        XCTAssertEqual(target,before)
        let new = try XCTUnwrap(LiteWoundSaveTarget.resolve(addingNew:true,selectedID:group.id,entered:location,groups:[group]))
        XCTAssertTrue(en.confirmation(new).hasPrefix("Wound: New wound"))
        XCTAssertEqual(new.name,"新增傷口"); XCTAssertTrue(new.woundID.isEmpty)
        XCTAssertNil(LiteWoundSaveTarget.resolve(addingNew:true,selectedID:"",entered:.init(side:"",site:""),groups:[]))
    }
    func testMissingDepthTranslationRetainsCaptureAndCannotSaveOrClear() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:folder) }
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let store = LiteStore(fileURL: folder.appendingPathComponent("records.json"))
        let vm = LiteMeasureVM(captureParticipation: { nil })
        XCTAssertEqual(en.text(vm.captureButtonTitle),"Photograph wound")
        _ = await vm.ingest(photo(),depth:nil)
        let bytes = try XCTUnwrap(vm.captureJPEG), note = try XCTUnwrap(vm.note)
        for copy in [en,zh,en] {
            XCTAssertFalse(copy.message(note).isEmpty)
            XCTAssertNil(vm.finishSavedCapture(in:store))
            XCTAssertEqual(vm.captureJPEG,bytes); XCTAssertEqual(vm.note,note)
            XCTAssertNil(vm.depth); XCTAssertNil(vm.estimate); XCTAssertNil(vm.cloudBinding)
        }
        XCTAssertEqual(en.text(vm.captureButtonTitle),"Retake photo")
        XCTAssertTrue(en.message(note).contains("has no LiDAR depth")); XCTAssertTrue(store.records.isEmpty)
    }
    func testMismatchedDepthRemainsUnboundAfterRenderingError() async throws {
        let vm = LiteMeasureVM(captureParticipation: { nil })
        let depth = DepthCapture(map: Array(repeating: 0.3,count:64),width:8,height:8,fx:8,fy:8,cx:4,cy:4,
            refWidth:8,refHeight:8,accuracy:"absolute",filtered:false,rgbWidth:16,rgbHeight:16,sourceExifOrientation:6)
        _ = await vm.ingest(photo(),depth:depth) // upright photo / rotated depth
        let note = try XCTUnwrap(vm.note), bytes = try XCTUnwrap(vm.captureJPEG)
        XCTAssertTrue(en.message(note).contains("could not be matched"))
        XCTAssertEqual(zh.message(note),note); XCTAssertEqual(vm.captureJPEG,bytes)
        XCTAssertNil(vm.depth); XCTAssertNil(vm.estimate); XCTAssertNil(vm.cloudBinding)
    }
    func testQualityMessagesRetainNumbersAndNeverReplaceMissingWithZero() {
        var e = result(); let original = liteVerdict(e)
        XCTAssertTrue(original.usable)
        XCTAssertTrue(en.projection(e).contains("9.91 cm²")); XCTAssertTrue(en.projection(e).contains("30 cm"))
        XCTAssertTrue(en.projection(e).contains("95%")); XCTAssertFalse(en.projection(e).contains("95%%"))
        XCTAssertTrue(original.warnings.allSatisfy { en.message($0) != $0 })
        XCTAssertEqual(liteVerdict(e).warnings,original.warnings)
        for fault in 0..<4 {
            e = result()
            switch fault { case 0:e.surfaceAreaCm2 = .nan; case 1:e.medianDistanceM = 0.2; case 2:e.coverage = 0.3; default:e.surfaceAreaCm2 = 15 }
            let v = liteVerdict(e); XCTAssertFalse(v.usable)
            XCTAssertNotNil(v.blocker); XCTAssertNotEqual(en.message(v.blocker!),v.blocker!)
        }
        XCTAssertTrue(en.text("本次沒有可提供的深度／體積參考值，不以零代替。").contains("not shown as zero"))
        XCTAssertTrue(en.text("通過方向、尺寸及深度覆蓋檢查，只表示資料具備計算條件，並不保證深度與照片在每一處都精確對齊。").contains("does not guarantee"))
    }
    func testUnknownErrorsAreNotRewrittenAsSuccessOrNewConsent() {
        let custom = "UNKNOWN_CAPTURE: 使用者名稱 %@；未確認"
        XCTAssertEqual(en.message(custom),custom)
        let known = "有 3 筆拍攝尚待核對。請解鎖後點「重新核對本機拍攝」。"
        XCTAssertTrue(en.message(known).contains("3 captures"))
        XCTAssertEqual(en.message("prefix " + known),"prefix " + known)
        XCTAssertTrue(en.message("已套用個人服務圈選。請確認邊界後存入紀錄；這次辨識不代表同意研究或已上傳研究資料。").contains("does not imply research consent"))
        XCTAssertTrue(en.message("此操作無法復原，不退還已使用的辨識額度，也不會撤回已傳送的研究資料。服務回執仍保留供核對。").contains("not withdrawn"))
    }
    func testRenderingServiceStateDoesNotChangeOperationDeadlineOrResubmitEligibility() throws {
        var r = LiteServiceRecord(captureID:String(repeating:"a",count:32),operation:String(repeating:"a",count:32),origin:"https://service.invalid",installation:"synthetic",imageSHA256:String(repeating:"b",count:64),width:16,height:16)
        r.retryAt = 1794379000
        for note in ["local","pending","quota","capacity","unavailable","expired","rejected","reservationExpired","cancelled","review"] {
            r.note = note
            let before = r, encoded = try JSONEncoder().encode(r), allowed = r.canResubmit
            XCTAssertNotEqual(en.message(r.message),r.message)
            XCTAssertEqual(zh.message(r.message),r.message)
            XCTAssertEqual(r,before); XCTAssertEqual(try JSONDecoder().decode(LiteServiceRecord.self,from:encoded),r)
            XCTAssertEqual(r.canResubmit,allowed); XCTAssertFalse(r.canResubmit,"A message must not authorize a new operation")
        }
    }
    func testResultCardRendersBothLanguagesWithoutChangingEstimate() throws {
        let e = result()
        for locale in [Locale(identifier:"en_US"), Locale(identifier:"zh_Hant_TW")] {
            let view = LiteResultCard(e:e,verdict:liteVerdict(e)).padding().frame(width:393).background(Color.white)
                .environment(\.locale,locale).environment(\.colorScheme,.light)
            let renderer = ImageRenderer(content:view); renderer.scale = 2
            let image = try XCTUnwrap(renderer.uiImage)
            XCTAssertGreaterThan(image.size.height,150)
            let attachment = XCTAttachment(image:image); attachment.name = "capture-result-"+locale.identifier
            attachment.lifetime = .keepAlways; add(attachment)
        }
        XCTAssertEqual(e.surfaceAreaCm2,10.08); XCTAssertEqual(e.coverage,0.95); XCTAssertNil(e.maxDepthMm)
    }
}
