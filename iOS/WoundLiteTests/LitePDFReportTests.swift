import XCTest
import PDFKit
@testable import WoundLite

final class LitePDFReportTests: XCTestCase {
    private func image() -> UIImage {
        let f = UIGraphicsImageRendererFormat(); f.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: 800, height: 600), format: f).image { c in
            UIColor(white: 0.96, alpha: 1).setFill(); c.fill(CGRect(x: 0, y: 0, width: 800, height: 600))
            UIColor.systemPink.setFill(); c.fill(CGRect(x: 240, y: 160, width: 320, height: 280))
            ("合成紙上標靶 / SYNTHETIC ONLY" as NSString).draw(at: CGPoint(x: 80, y: 520),
                withAttributes: [.font: UIFont.systemFont(ofSize: 24), .foregroundColor: UIColor.black])
        }
    }
    private func record() -> LiteRecord {
        let d = DepthCapture(map: Array(repeating: 0.3, count: 32*24), width: 32, height: 24,
            fx: 30, fy: 31, cx: 15, cy: 11, refWidth: 32, refHeight: 24,
            accuracy: "absolute", filtered: false, rgbWidth: 800, rgbHeight: 600, sourceExifOrientation: 1)
        let r = DepthAreaResult(surfaceAreaCm2: 10.08, projectedAreaCm2: 9.91,
            coverage: 1, medianDistanceM: 0.3, tiltDeg: 0)
        return LiteRecord(id: "private-record-secret", dateISO: "2026-10-28T01:00:00Z",
            surfaceCm2: 10.08, projectedCm2: 9.91, tiltDeg: 0, volumeMl: 9.87, maxDepthMm: 6.54,
            quality: "ok", imageName: "private-image-secret", source: "manual",
            polysJson: "[[[240,160],[560,160],[560,440],[240,440]]]", depthName: "private-depth-secret",
            woundID: "private-group-secret", woundName: "合成驗收紀錄",
            woundLocation: LiteWoundLocation(side: "right", site: "heel"),
            measurementContext: LiteMeasurementContext(depth: d, result: r, warnings: []))
    }
    func testOnePageChineseReportContainsCorrectSourcesWithoutInternalIdentifiersOrVolume() throws {
        let data = LitePDFReport.render(record: record(), image: image())
        let pdf = try XCTUnwrap(PDFDocument(data: data)); let text = try XCTUnwrap(pdf.string)
        XCTAssertEqual(pdf.pageCount, 1)
        for value in ["WoundAILite", "10.08", "9.91", "LiDAR", "相機方向投影估算", "免貼紙", "不提供診斷"] {
            XCTAssertTrue(text.contains(value), value)
        }
        for value in ["private-", "9.87", "6.54", "appAccountToken", "雲端已確認"] { XCTAssertFalse(text.contains(value), value) }
        XCTAssertLessThan(data.count, 2_000_000)
        let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "com.adobe.pdf")
        attachment.name = "WoundAILite-synthetic-single-record.pdf"; attachment.lifetime = .keepAlways; add(attachment)
    }
    func testLegacyOrUnknownScaleDoesNotExportAnUnverifiedNumber() {
        var r = record(); r.measurementContext = nil
        XCTAssertTrue(LitePDFReport.areaLines(r, image: image()).joined().contains("未提供面積"))
        r = record(); r.measurementContext?.areaSource = "sticker_plane"
        XCTAssertFalse(LitePDFReport.areaLines(r, image: image()).joined().contains("10.08"))
        XCTAssertEqual(r.surfaceCm2, 10.08)
    }
    func testInvalidDepthNumbersAndInsufficientCoverageAreNotPublished() {
        var r = record(); r.measurementContext?.coverage = 0.1
        XCTAssertFalse(LitePDFReport.areaLines(r, image: image()).joined().contains("10.08"))
        r = record(); r.surfaceCm2 = .nan
        XCTAssertTrue(LitePDFReport.areaLines(r, image: image()).joined().contains("未提供面積"))
    }
    func testWrongDimensionsOrOrientationNeverMisplaceAnOutline() {
        var r = record(); r.measurementContext?.imageWidth = 801
        XCTAssertNil(LitePDFReport.polygons(r, image: image()))
        let rotated = UIImage(cgImage: image().cgImage!, scale: 1, orientation: .right)
        XCTAssertNil(LitePDFReport.polygons(record(), image: rotated))
        XCTAssertFalse(LitePDFReport.areaLines(record(), image: rotated).joined().contains("10.08"))
    }
    func testMalformedAndOutOfBoundsContoursAreRejectedWithoutInventingAnotherMask() {
        for json in ["[[[240],[560,160],[560,440]]]", "[[[240,160],[800,160],[560,440]]]", "[]", "broken"] {
            var r = record(); r.polysJson = json
            XCTAssertNil(LitePDFReport.polygons(r, image: image()))
        }
        XCTAssertEqual(LitePDFReport.polygons(record(), image: image())?.first?.first, [240,160])
    }
    func testMissingPhotoStillProducesATruthfulReportWithoutCloudDownload() throws {
        let pdf = try XCTUnwrap(PDFDocument(data: LitePDFReport.render(record: record(), image: nil)))
        XCTAssertTrue(pdf.string?.contains("本機照片已不存在") == true)
        XCTAssertFalse(pdf.string?.contains("10.08") == true)
    }
    func testQualityWarningsAreKeptAndLongNotesPaginate() throws {
        var r = record()
        r.measurementContext?.warnings = (1...12).map { "第\($0)項拍攝備註：" + String(repeating: "光線不足時請留意圈選範圍。", count: 8) }
        let pdf = try XCTUnwrap(PDFDocument(data: LitePDFReport.render(record: r, image: image())))
        XCTAssertGreaterThan(pdf.pageCount, 1)
        XCTAssertTrue(pdf.string?.contains("第12項拍攝備註") == true)
    }
    @MainActor
    func testExportDoesNotWriteRecordIndexOrCreateRevision() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: file); XCTAssertTrue(store.add(record()))
        let before = try Data(contentsOf: file)
        _ = LitePDFReport.render(record: store.records[0], image: image())
        XCTAssertEqual(try Data(contentsOf: file), before)
        XCTAssertTrue(store.syncingRecordIDs.isEmpty)
    }
    func testTemporaryCopyIsExcludedFromBackupAndCleanupPreservesOtherFiles() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = try LitePDFShare(data: LitePDFReport.render(record: record(), image: nil), directory: directory)
        let second = try LitePDFShare(data: LitePDFReport.render(record: record(), image: image()), directory: directory)
        XCTAssertNotEqual(first.url, second.url)
        XCTAssertEqual(try first.url.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        let original = directory.appendingPathComponent("original.enc"); try Data("preserve".utf8).write(to: original)
        first.remove(); XCTAssertFalse(FileManager.default.fileExists(atPath: first.url.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.url.path))
        LitePDFShare.purgeAfterLaunch(directory: directory)
        XCTAssertFalse(FileManager.default.fileExists(atPath: second.url.path))
        XCTAssertEqual(try Data(contentsOf: original), Data("preserve".utf8))
    }
    func testNonPDFBytesCannotBeSharedAsAReport() {
        XCTAssertThrowsError(try LitePDFShare(data: Data("invalid".utf8)))
    }
}
