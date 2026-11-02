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
            measurementContext: LiteMeasurementContext(depth: d, result: r, warnings: []), contourOrigin: .manual)
    }
    func testSingleReportLabelsVerifiedAndHistoricalContourVersions() throws {
        var row = record()
        row.contourOrigin = .cloud(route: "student", contract: LiteContourOrigin.studentV1)
        let current = try XCTUnwrap(PDFDocument(data: LitePDFReport.render(record: row, image: image()))?.string)
        XCTAssertTrue(current.contains("Student 第 1 版"))
        row.contourOrigin = nil
        let historical = try XCTUnwrap(PDFDocument(data: LitePDFReport.render(record: row, image: image()))?.string)
        XCTAssertTrue(historical.contains("圈選版本未確認"))
    }

    func testTimelinePDFIsChronologicalPaginatedAndIsolatedToOneWound() throws {
        let group = LiteWoundGroup(id:"A",name:"合成標靶 A",location:.init(side:"right", site:"heel"))
        var entries = (1...4).map { day -> LiteRecord in
            var row = record()
            row.id = "private-\(day)"
            row.woundID = group.id
            row.dateISO = "2026-10-2\(day)T01:00:00Z"
            return row
        }
        entries[1].surfaceCm2 = 8.50
        entries[1].projectedCm2 = 8.30
        entries[2].measurementContext?.coverage = 0.1
        entries[2].surfaceCm2 = 123.45
        entries[3].measurementContext = nil
        entries[3].surfaceCm2 = 7.25
        entries[3].projectedCm2 = 7.03
        entries[3].contourOrigin = nil
        var foreign = record()
        foreign.woundID = "B"
        foreign.surfaceCm2 = 678.91
        let selection = try LiteTimelineSelection(group:group,records:[foreign]+entries.reversed())
        XCTAssertEqual(selection.rows.map(\.id), entries.map(\.id))
        let img = image()
        let file = try LiteTimelinePDF.export(selection,photos:true) { _ in img }
        defer { file.remove() }
        let data = try Data(contentsOf:file.url), pdf = try XCTUnwrap(PDFDocument(data:data))
        XCTAssertEqual(pdf.pageCount,entries.count+1)
        let text = try XCTUnwrap(pdf.string)
        for value in ["10.08","9.91","8.50","未提供面積","免貼紙","合成標靶 A","2026/10/24",
                      "表面積趨勢","可繪 3 / 4 筆","舊版 LiDAR 表面積：7.25","青色細線標示已存傷口輪廓"] {
            XCTAssertTrue(text.contains(value),value)
        }
        for value in ["123.45","678.91","private-","9.87","6.54"] { XCTAssertFalse(text.contains(value),value) }
        let attachment = XCTAttachment(data:data,uniformTypeIdentifier:"com.adobe.pdf")
        attachment.name = "WoundAILite-synthetic-wound-timeline.pdf"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testTimelineDateRangeUsesLocalDaysAndDoesNotGuessMissingDates() throws {
        var calendar = Calendar(identifier:.gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier:"Asia/Taipei"))
        let day = try XCTUnwrap(ISO8601DateFormatter().date(from:"2026-10-21T01:00:00Z"))
        var rows = ["2026-10-20T15:59:59Z","2026-10-20T16:00:00Z","2026-10-21T15:59:59.500Z",
                    "2026-10-21T16:00:00Z","unknown"].map { date -> LiteRecord in
            var row = record(); row.id = date; row.dateISO = date; row.woundID = "A"; return row
        }
        rows.reverse()
        let group = LiteWoundGroup(id:"A",name:"A")
        let selected = try LiteTimelineSelection(group:group,records:rows,first:day,last:day,calendar:calendar)
        XCTAssertEqual(selected.rows.map(\.id), ["2026-10-20T16:00:00Z","2026-10-21T15:59:59.500Z"])
        XCTAssertEqual(selected.missingDates,1)
        XCTAssertEqual(try LiteTimelineSelection(group:group,records:rows).rows.last?.id,"unknown")
        let file = try LiteTimelinePDF.export(selected,photos:false) { _ in self.image() }
        defer { file.remove() }
        XCTAssertEqual(PDFDocument(url:file.url)?.pageCount,1)
        XCTAssertThrowsError(try LiteTimelineSelection(group:group,records:rows,first:day,last:day.addingTimeInterval(-86400)))
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
        var r = record(); r.measurementContext = nil; r.depthName = nil
        XCTAssertTrue(LitePDFReport.areaLines(r, image: image()).joined().contains("未提供面積"))
        r = record(); r.measurementContext?.areaSource = "sticker_plane"
        XCTAssertFalse(LitePDFReport.areaLines(r, image: image()).joined().contains("10.08"))
        XCTAssertEqual(r.surfaceCm2, 10.08)
    }
    func testLegacyReportUsesOriginalNumbersWithAnExplicitHistoryLabel() throws {
        var r = record(); r.measurementContext = nil; r.contourOrigin = nil
        let original = r.polysJson
        let img = image()
        r.cloudSync = LiteCloudSyncState(binding: .init(server:"fixture", anonID:"fixture", imageID:"fixture",
                                                        imageW:800, imageH:600))
        let lines = LitePDFReport.areaLines(r, image: img)
        XCTAssertTrue(lines.contains("舊版 LiDAR 表面積：10.08 cm²"))
        XCTAssertTrue(lines.contains("舊版相機方向投影：9.91 cm²"))
        XCTAssertEqual(LitePDFReport.polygons(r, image: img)?.first?.count, 4)
        r.cloudSync?.binding.imageW = 801
        XCTAssertNil(LitePDFReport.polygons(r, image: img))
        XCTAssertFalse(LitePDFReport.areaLines(r, image: img).joined().contains("10.08"))
        XCTAssertEqual(r.polysJson, original)
    }

    func testChartBreaksAtMissingDataAndKeepsLegacySeparate() throws {
        var rows = (0...5).map { i in
            var r = record(); r.id = "r\(i)"; r.dateISO = "2026-10-2\(i)T01:02:03.500Z"; return r
        }
        rows[4].measurementContext = nil
        rows[5].contourOrigin = nil
        let wound = try XCTUnwrap(rows[0].woundID)
        let current = LiteWoundTrend.points(woundID: wound, records: rows, series: .current,
                                            include: { $0.id != "r2" })
        XCTAssertEqual(current.map(\.id), ["r0","r1","r3","r5"])
        XCTAssertEqual(current.map(\.lineGroup), ["r0","r0","r3",nil])
        XCTAssertEqual(LiteWoundTrend.points(woundID: wound, records: rows, series: .legacy).map(\.id), ["r4"])
    }

    func testUnknownAreaKeepsMaskAndSinglePointIsNotLostWhenPhotosHidden() throws {
        var row = record()
        let img = image(), group = LiteWoundGroup(id:try XCTUnwrap(row.woundID), name:"合成標靶")
        func exportText(photos: Bool) throws -> String {
            let selection = try LiteTimelineSelection(group: group, records: [row])
            let file = try LiteTimelinePDF.export(selection, photos: photos) { _ in img }
            defer { file.remove() }
            return try XCTUnwrap(PDFDocument(url: file.url)?.string)
        }
        let value = try exportText(photos: false)
        XCTAssertTrue(value.contains("可繪 1 / 1 筆"))
        XCTAssertTrue(value.contains("10.08"))
        XCTAssertTrue(value.contains("照片與圈選依選項省略"))
        row.measurementContext?.coverage = 0.1
        let rejected = try exportText(photos: true)
        XCTAssertTrue(rejected.contains("可繪 0 / 1 筆"))
        XCTAssertTrue(rejected.contains("青色細線標示已存傷口輪廓"))
        XCTAssertFalse(rejected.contains("10.08"))
    }
    func testInvalidDepthNumbersAndInsufficientCoverageAreNotPublished() {
        var r = record(); r.measurementContext?.coverage = 0.1
        XCTAssertFalse(LitePDFReport.areaLines(r, image: image()).joined().contains("10.08"))
        r = record(); r.surfaceCm2 = .nan
        XCTAssertTrue(LitePDFReport.areaLines(r, image: image()).joined().contains("未提供面積"))
    }
    func testLowCoverageCannotSupplyAHistoryPointOrPercentageBaseline() throws {
        var first = record()
        first.id = "first"; first.dateISO = "2026-10-26T01:00:00Z"
        var bad = first
        bad.id = "bad"; bad.dateISO = "2026-10-27T01:00:00Z"
        bad.surfaceCm2 = 20; bad.projectedCm2 = 20
        bad.measurementContext?.coverage = 0.1
        let latest = record()
        let records = [latest, bad, first]
        let original = try JSONEncoder().encode(bad)

        XCTAssertTrue(LitePDFReport.areaLines(bad, image: image()).joined().contains("未提供面積"))
        XCTAssertNil(bad.areaSeries)
        XCTAssertNil(LiteGrouping.changePercent(bad, in: records))
        XCTAssertEqual(LiteGrouping.changePercent(latest, in: records), 0)
        XCTAssertEqual(LiteWoundTrend.points(woundID: try XCTUnwrap(first.woundID), records: records).map(\.id),
                       [first.id, latest.id])
        XCTAssertEqual(try JSONDecoder().decode(LiteRecord.self, from: original).surfaceCm2, 20)
    }

    func testUnknownOrientationAndInvalidDepthDimensionsCannotBeReportedAsCurrentArea() {
        var bad = record()
        bad.measurementContext?.orientation = "unknown"
        XCTAssertNil(bad.areaSeries)
        XCTAssertTrue(LitePDFReport.areaLines(bad, image: image()).joined().contains("未提供面積"))
        bad = record(); bad.measurementContext?.depthHeight = 1
        XCTAssertNil(bad.areaSeries)
        XCTAssertTrue(LitePDFReport.areaLines(bad, image: image()).joined().contains("未提供面積"))
    }

    func testIncompatibleAspectAndImpossibleStoredDistanceAreWithheldWithoutChangingTheRecord() {
        var r = record()
        r.measurementContext?.depthHeight = 32
        XCTAssertNil(r.areaSeries)
        XCTAssertTrue(LitePDFReport.areaLines(r, image: image()).joined().contains("未提供面積"))
        r = record(); r.measurementContext?.distanceM = .greatestFiniteMagnitude
        XCTAssertNil(r.areaSeries)
        XCTAssertFalse(LitePDFReport.areaLines(r, image: image()).joined().contains("10.08"))
        XCTAssertEqual(r.surfaceCm2, 10.08)
    }

    @MainActor
    func testReadingAndExportingHistoryLeavesSavedIndexIntactAndDoesNotQueueResearch() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let index = directory.appendingPathComponent("records.json")
        let store = LiteStore(fileURL: index)
        var r = record(); r.measurementContext?.coverage = 0.7
        XCTAssertTrue(store.add(r))
        r.id = "low-coverage"; r.measurementContext?.coverage = 0.1
        XCTAssertTrue(store.add(r))
        let before = try Data(contentsOf: index)
        let reloaded = LiteStore(fileURL: index)
        let points = LiteWoundTrend.points(woundID: try XCTUnwrap(r.woundID), records: reloaded.records)
        XCTAssertEqual(points.map(\.area), [10.08])
        for row in reloaded.records {
            let document = try XCTUnwrap(PDFDocument(data: LitePDFReport.render(record: row, image: image())))
            let text = try XCTUnwrap(document.string)
            if row.id == "low-coverage" {
                XCTAssertNil(row.areaSeries)
                XCTAssertFalse(text.contains("10.08"))
            } else {
                XCTAssertEqual(row.areaSeries, .current)
                XCTAssertTrue(text.contains("10.08"))
                XCTAssertTrue(text.contains("補值"))
            }
            XCTAssertNil(row.cloudSync)
        }
        XCTAssertEqual(try Data(contentsOf: index), before)
        XCTAssertTrue(reloaded.syncingRecordIDs.isEmpty)
    }
    func testWrongDimensionsOrOrientationNeverMisplaceAnOutline() {
        var r = record(); r.measurementContext?.imageWidth = 801
        XCTAssertNil(LitePDFReport.polygons(r, image: image()))
        let rotated = UIImage(cgImage: image().cgImage!, scale: 1, orientation: .right)
        XCTAssertNil(LitePDFReport.polygons(record(), image: rotated))
        XCTAssertFalse(LitePDFReport.areaLines(record(), image: rotated).joined().contains("10.08"))
    }
    func testSavedOutlinePreservesInteriorColourAndRemainsThinAtItsRegisteredEdge() throws {
        let row = record()
        let pageSize = CGSize(width: 595.28, height: 841.89)
        let photo = image()
        let selection = try LiteTimelineSelection(group:.init(id:try XCTUnwrap(row.woundID), name:"TEST"),
                                                  records:[row])
        let file = try LiteTimelinePDF.export(selection, photos:true) { _ in photo }
        defer { file.remove() }
        let page = try XCTUnwrap(PDFDocument(url:file.url)?.page(at:0))
        let scale: CGFloat = 4
        let raster = CGSize(width:pageSize.width * scale,height:pageSize.height * scale)
        let thumb = page.thumbnail(of:raster, for:.mediaBox)
        func pixel(_ image: UIImage, _ x: CGFloat, _ y: CGFloat, size: CGSize) throws -> [UInt8] {
            var bytes = [UInt8](repeating:0, count:4)
            try bytes.withUnsafeMutableBytes { raw in
                let ctx = try XCTUnwrap(CGContext(data:raw.baseAddress, width:1, height:1, bitsPerComponent:8,
                    bytesPerRow:4, space:CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue))
                ctx.draw(try XCTUnwrap(image.cgImage), in:CGRect(x:-x, y:y-size.height,
                                                                 width:size.width, height:size.height))
            }
            return bytes
        }
        let inside = try pixel(thumb,297 * scale,504 * scale,size:raster)
        let original = try pixel(photo,400,300,size:photo.size)
        for channel in 0..<3 {
            XCTAssertLessThanOrEqual(abs(Int(inside[channel]) - Int(original[channel])),12)
        }
        let outside = try pixel(thumb,163 * scale,405 * scale,size:raster)
        XCTAssertGreaterThan(outside[0],228)
        XCTAssertLessThan(abs(Int(outside[0]) - Int(outside[1])),5)
        // At four rendered pixels per printed point, the left edge of this
        // 800 × 600 fixture is near x=232 pt. The outline must not fill the box.
        let samples = try (912...946).map { try pixel(thumb,CGFloat($0),504 * scale,size:raster) }
        let teal = samples.filter { Int($0[1]) - Int($0[0]) > 40 && Int($0[2]) - Int($0[0]) > 40 }
        XCTAssertGreaterThanOrEqual(teal.count,1)
        XCTAssertLessThanOrEqual(teal.count,5)
    }
    func testReportPhotoUsesSourcePixelsAndBoundsLargePhotographs() throws {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 3
        let portrait = UIGraphicsImageRenderer(size: CGSize(width:240,height:640), format:format).image { c in
            UIColor.darkGray.setFill()
            c.fill(CGRect(x:0,y:0,width:240,height:640))
        }
        let raster = try XCTUnwrap(LitePDFReport.reportPhoto(portrait).cgImage)
        XCTAssertEqual(raster.width,600)
        XCTAssertEqual(raster.height,1600)
        let small = try XCTUnwrap(LitePDFReport.reportPhoto(image()).cgImage)
        XCTAssertEqual(small.width,800)
        XCTAssertEqual(small.height,600)
    }

    func testDetailReportKeepsEachDatedPhotoOnItsOwnPage() throws {
        func target(dark: Bool) -> UIImage {
            let f = UIGraphicsImageRendererFormat()
            f.scale = 1
            return UIGraphicsImageRenderer(size: CGSize(width:800,height:600),format:f).image { painter in
                UIColor(white:dark ? 0.16 : 0.95,alpha:1).setFill()
                painter.fill(CGRect(x:0,y:0,width:800,height:600))
                let box = CGRect(x:240,y:160,width:320,height:280)
                UIColor(red:0.62,green:0.22,blue:0.27,alpha:1).setFill()
                painter.fill(box)
                painter.cgContext.saveGState()
                painter.cgContext.clip(to:box)
                for i in 0..<95 {
                    let x = 240 + (i * 71) % 320, y = 160 + (i * 37) % 280
                    UIColor(red:0.84,green:0.43,blue:0.45,alpha:0.85).setFill()
                    painter.cgContext.fillEllipse(in:CGRect(x:x,y:y,width:6+i%9,height:3+i%5))
                }
                painter.cgContext.restoreGState()
                let ink = dark ? UIColor.white : UIColor.black
                ("合成材質標靶  |  SYNTHETIC" as NSString).draw(at:CGPoint(x:70,y:502),
                    withAttributes:[.font:UIFont.systemFont(ofSize:22),.foregroundColor:ink])
            }
        }
        var first=record(), last=record()
        first.id="detail-A"; first.dateISO="2026-10-21T01:00:00Z"
        last.id="detail-B"; last.dateISO="2026-10-22T01:00:00Z"
        last.surfaceCm2=8.25; last.projectedCm2=8.02
        let group=LiteWoundGroup(id:try XCTUnwrap(first.woundID),name:"合成材質標靶",location:first.woundLocation)
        let selection=try LiteTimelineSelection(group:group,records:[last,first])
        let photos=[first.id:target(dark:false),last.id:target(dark:true)]
        let copy=try LiteTimelinePDF.export(selection,photos:true) { photos[$0.id] }
        defer { copy.remove() }
        let doc=try XCTUnwrap(PDFDocument(url:copy.url))
        XCTAssertEqual(doc.pageCount,3)
        XCTAssertTrue(try XCTUnwrap(doc.page(at:0)?.string).contains("觀察紀錄索引"))
        XCTAssertTrue(try XCTUnwrap(doc.page(at:1)?.string).contains("10.08"))
        XCTAssertTrue(try XCTUnwrap(doc.page(at:2)?.string).contains("8.25"))
        let attachment=XCTAttachment(data:try Data(contentsOf:copy.url),uniformTypeIdentifier:"com.adobe.pdf")
        attachment.name="WoundAILite-timeline-photo-detail.pdf"
        attachment.lifetime = .keepAlways
        add(attachment)
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
