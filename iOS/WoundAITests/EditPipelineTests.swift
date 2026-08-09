import XCTest
import CoreGraphics
@testable import WoundMeasurementApp

/**
 修邊資料鏈的單元測試：多輪廓 JSON、遮罩填充／邊界追蹤／RDP 往返、網格分類器。

 這一批測的是**資料正確性**路徑——畫布只是它們的操作介面。每一條都對應
 Android 端修過的一個真實 bug（多輪廓丟失、椒鹽底稿、退化多邊形入佇列）。
 */

// MARK: - PolygonJson

final class PolygonJsonTests: XCTestCase {

    /// 單一輪廓必須寫成**舊格式**（`[[x,y],…]`），舊版程式與舊紀錄才讀得懂。
    func testSinglePolygonUsesLegacyFormat() {
        let poly: [[Int]] = [[0, 0], [10, 0], [10, 10]]
        let js = PolygonJson.toJson([poly])
        XCTAssertEqual(js, "[[0,0],[10,0],[10,10]]")
        // 往返
        let back = PolygonJson.parse(js)
        XCTAssertEqual(back, [poly])
    }

    func testMultiPolygonRoundTrip() {
        let a: [[Int]] = [[0, 0], [10, 0], [10, 10], [0, 10]]
        let b: [[Int]] = [[50, 50], [60, 50], [55, 60]]
        let js = PolygonJson.toJson([a, b])
        XCTAssertNotNil(js)
        XCTAssertTrue(js!.hasPrefix("[[["), "多輪廓必須是巢狀三層格式")
        let back = PolygonJson.parse(js)
        XCTAssertEqual(back, [a, b])
    }

    /// Android DB 匯過來的舊格式（org.json 輸出，無空白）必須解得開。
    func testParsesAndroidLegacyString() {
        let js = "[[100,200],[300,200],[300,400],[100,400]]"
        let back = PolygonJson.parse(js)
        XCTAssertEqual(back.count, 1)
        XCTAssertEqual(back[0].count, 4)
        XCTAssertEqual(back[0][2], [300, 400])
    }

    /// 退化輸入（<3 點）不得產生輪廓——`pendingAnnotationCount` 用
    /// `gtPolygon IS NOT NULL` 判斷可否送標註，退化多邊形會把算不出面積的 GT 排進佇列。
    func testDegenerateInputsRejected() {
        XCTAssertNil(PolygonJson.toJson([]))
        XCTAssertNil(PolygonJson.toJson([[[1, 2], [3, 4]]]))
        XCTAssertEqual(PolygonJson.parse(nil), [])
        XCTAssertEqual(PolygonJson.parse(""), [])
        XCTAssertEqual(PolygonJson.parse("not json"), [])
        XCTAssertEqual(PolygonJson.parse("[]"), [])
    }

    /// 混合輸入：多輪廓格式裡有一個退化輪廓 → 只丟掉那一個，不整包作廢。
    func testMixedDegenerateDropped() {
        let js = "[[[0,0],[10,0],[10,10]],[[1,1],[2,2]]]"
        let back = PolygonJson.parse(js)
        XCTAssertEqual(back.count, 1)
        XCTAssertEqual(back[0].count, 3)
    }

    func testLargestPicksMostPoints() {
        let small: [[Int]] = [[0, 0], [1, 0], [1, 1]]
        let big: [[Int]] = [[0, 0], [9, 0], [9, 9], [0, 9], [0, 5]]
        XCTAssertEqual(PolygonJson.largest([small, big]).count, 5)
        XCTAssertEqual(PolygonJson.largest([]), [])
    }
}

// MARK: - MaskTrace

final class MaskTraceTests: XCTestCase {

    /// 已知矩形：填充後像素數要等於面積、追蹤要回一個元件、RDP 後仍涵蓋同範圍。
    func testFillTraceRoundTripOnRectangle() {
        let mw = 64, mh = 64
        var mask = [UInt8](repeating: 0, count: mw * mh)
        let rect: [[Int]] = [[8, 8], [40, 8], [40, 30], [8, 30]]
        MaskTrace.scanlineFill(rect, s: 1.0, mw: mw, mh: mh, into: &mask)

        let filled = mask.reduce(0) { $0 + Int($1) }
        // even-odd 掃描線以像素中心取樣：33×23 的封閉矩形，容忍邊緣 ±2 列/行。
        XCTAssertGreaterThan(filled, 30 * 20)
        XCTAssertLessThan(filled, 35 * 25)

        let bounds = MaskTrace.traceAllBoundaries(mask, mw: mw, mh: mh, minPx: 16)
        XCTAssertEqual(bounds.count, 1, "單一矩形只能有一個連通元件")

        let simplified = MaskTrace.rdp(bounds[0], eps: 1.5)
        XCTAssertGreaterThanOrEqual(simplified.count, 4)
        XCTAssertLessThan(simplified.count, bounds[0].count, "RDP 必須有精簡效果")
        // 簡化後的點都要落在原始外框附近
        for p in simplified {
            XCTAssertTrue(p[0] >= 6 && p[0] <= 42 && p[1] >= 6 && p[1] <= 32,
                          "RDP 點 (\(p[0]), \(p[1])) 跑出矩形範圍")
        }
    }

    /// **兩個分離的傷口必須都被追出來**——這正是 Android 2026-08-07 修掉的 bug：
    /// 只取最大元件，第二個傷口被標成背景，等於教模型「那不是傷口」。
    func testTwoComponentsBothTraced() {
        let mw = 100, mh = 40
        var mask = [UInt8](repeating: 0, count: mw * mh)
        MaskTrace.scanlineFill([[5, 5], [30, 5], [30, 30], [5, 30]],
                               s: 1.0, mw: mw, mh: mh, into: &mask)
        MaskTrace.scanlineFill([[60, 10], [90, 10], [90, 25], [60, 25]],
                               s: 1.0, mw: mw, mh: mh, into: &mask)
        let bounds = MaskTrace.traceAllBoundaries(mask, mw: mw, mh: mh, minPx: 16)
        XCTAssertEqual(bounds.count, 2)
        // 由大到小排序：第一個是 26×26 的那塊
        XCTAssertGreaterThan(bounds[0].count, bounds[1].count)
    }

    /// 幾像素的孤立點（筆刷邊緣抗鋸齒殘留）不得成為獨立傷口——那是垃圾標註。
    func testTinySpecksFiltered() {
        let mw = 50, mh = 50
        var mask = [UInt8](repeating: 0, count: mw * mh)
        MaskTrace.scanlineFill([[10, 10], [35, 10], [35, 35], [10, 35]],
                               s: 1.0, mw: mw, mh: mh, into: &mask)
        mask[3 * mw + 3] = 1    // 一顆孤立像素
        mask[3 * mw + 4] = 1
        let bounds = MaskTrace.traceAllBoundaries(mask, mw: mw, mh: mh, minPx: 64)
        XCTAssertEqual(bounds.count, 1)
    }

    func testEmptyMask() {
        XCTAssertEqual(MaskTrace.traceAllBoundaries([UInt8](repeating: 0, count: 100),
                                                    mw: 10, mh: 10).count, 0)
    }

    /// 縮放＋位移填充：影像座標的多邊形要落在柵格的正確位置（修邊 ROI 的座標鏈）。
    func testFillWithScaleAndOffset() {
        let mw = 20, mh = 20
        var mask = [UInt8](repeating: 0, count: mw * mh)
        // 影像座標 (100,100)-(140,140)，ROI 原點 (100,100)、縮放 0.5 → 柵格 (0,0)-(20,20)
        MaskTrace.scanlineFill([[100, 100], [140, 100], [140, 140], [100, 140]],
                               s: 0.5, mw: mw, mh: mh, into: &mask, ox: 100, oy: 100)
        XCTAssertGreaterThan(mask.reduce(0) { $0 + Int($1) }, 15 * 15)
        XCTAssertEqual(mask[0], 1, "左上角應在遮罩內")
    }
}

// MARK: - TissueSeg.classify

final class TissueSegClassifyTests: XCTestCase {

    /// 純色合成圖 → CGImage。
    private func solidImage(r: UInt8, g: UInt8, b: UInt8, w: Int, h: Int) -> CGImage? {
        var px = [UInt8](repeating: 255, count: w * h * 4)
        for i in 0..<(w * h) {
            px[i * 4] = r; px[i * 4 + 1] = g; px[i * 4 + 2] = b; px[i * 4 + 3] = 255
        }
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let provider = CGDataProvider(data: Data(px) as CFData) else { return nil }
        return CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32,
                       bytesPerRow: w * 4, space: cs,
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                       provider: provider, decode: nil, shouldInterpolate: false,
                       intent: .defaultIntent)
    }

    /// 中性灰輸入下（增益＝1）分類穩定，且輸出是**修邊畫面碼**、經 3×3 多數決後整片一致。
    func testUniformInputGivesUniformCodes() throws {
        // 肉芽紅（分類器碼 3 → 修邊碼 1）。給 identity 增益，避免灰世界把純色拉去白。
        let img = try XCTUnwrap(solidImage(r: 190, g: 60, b: 70, w: 64, h: 64))
        let inside = [UInt8](repeating: 1, count: 32 * 32)
        let out = try XCTUnwrap(TissueSeg.classify(img, x0: 0, y0: 0, x1: 64, y1: 64,
                                                   gw: 32, gh: 32, inside: inside,
                                                   wbGains: [1, 1, 1]))
        // 直接對照 classifyPixel 的預期（同一組增益）
        let expectCls = TissueClassifierV2.classifyPixel(190, 60, 70)
        let expectEdit = TissueSeg.clsToEdit[expectCls]
        let inner = (8..<24).flatMap { y in (8..<24).map { x in out[y * 32 + x] } }
        XCTAssertTrue(inner.allSatisfy { $0 == expectEdit },
                      "純色圖經多數決後內部必須整片同碼（期望 \(expectEdit)）")
    }

    /// 遮罩外必須是 0——分類範圍就是遮罩範圍，皮膚與背景不參與。
    func testOutsideMaskIsZero() throws {
        let img = try XCTUnwrap(solidImage(r: 190, g: 60, b: 70, w: 64, h: 64))
        var inside = [UInt8](repeating: 0, count: 32 * 32)
        for y in 10..<20 { for x in 10..<20 { inside[y * 32 + x] = 1 } }
        let out = try XCTUnwrap(TissueSeg.classify(img, x0: 0, y0: 0, x1: 64, y1: 64,
                                                   gw: 32, gh: 32, inside: inside,
                                                   wbGains: [1, 1, 1]))
        XCTAssertEqual(out[0], 0)
        XCTAssertEqual(out[31 * 32 + 31], 0)
        XCTAssertNotEqual(out[15 * 32 + 15], 0)
    }

    func testDegenerateRegionReturnsNil() throws {
        let img = try XCTUnwrap(solidImage(r: 100, g: 100, b: 100, w: 8, h: 8))
        XCTAssertNil(TissueSeg.classify(img, x0: 0, y0: 0, x1: 1, y1: 1,
                                        gw: 2, gh: 2, inside: [1, 1, 1, 1]))
        XCTAssertNil(TissueSeg.classify(img, x0: 0, y0: 0, x1: 8, y1: 8,
                                        gw: 4, gh: 4,
                                        inside: [UInt8](repeating: 0, count: 16)),
                     "遮罩全空（n=0）要回 nil，不是整片猜一個類別")
    }
}

// MARK: - ClassifyResult 多輪廓解析

final class MultiPolygonParseTests: XCTestCase {

    private func makeResult(_ s2Extra: String) -> ClassifyResult? {
        let json = """
        {"stage2_segment": {"confidence": 0.9, "route": "student",
          "wound_polygon": [[0,0],[10,0],[10,10]] \(s2Extra)},
         "stage3_calibrate": {}, "stage4_tissue": {"tissue_frac": {}},
         "stage5_severity": {}, "image_w": 100, "image_h": 100}
        """
        guard let j = JSONAny(data: Data(json.utf8)) else { return nil }
        return BackendClient.parseClassify(j)
    }

    /// 舊版後端（無 `wound_polygons` 鍵）→ 退回單輪廓包一層。
    func testLegacyBackendFallsBackToSingle() throws {
        let r = try XCTUnwrap(makeResult(""))
        XCTAssertEqual(r.woundPolygons.count, 1)
        XCTAssertEqual(r.woundPolygons[0], [[0, 0], [10, 0], [10, 10]])
    }

    func testMultiPolygonParsed() throws {
        let r = try XCTUnwrap(makeResult(
            ", \"wound_polygons\": [[[0,0],[10,0],[10,10]],[[50,50],[60,50],[60,60],[50,60]]]"))
        XCTAssertEqual(r.woundPolygons.count, 2)
        XCTAssertEqual(r.woundPolygons[1].count, 4)
        // 單輪廓欄位仍是最大／第一個，舊路徑不受影響
        XCTAssertEqual(r.woundPolygon, [[0, 0], [10, 0], [10, 10]])
    }
}

// MARK: - Measurement 多輪廓存取

final class MeasurementPolygonTests: XCTestCase {

    func testPolygonsAccessorHandlesBothFormats() {
        var m = Measurement()
        m.gtPolygon = "[[1,2],[3,4],[5,6]]"
        XCTAssertEqual(m.polygons.count, 1)
        XCTAssertEqual(m.polygonPoints.count, 3)

        m.gtPolygon = "[[[1,2],[3,4],[5,6]],[[7,8],[9,10],[11,12],[13,14]]]"
        XCTAssertEqual(m.polygons.count, 2)
        // largest 取點數最多的那一個
        XCTAssertEqual(m.polygonPoints.count, 4)

        m.gtPolygon = nil
        XCTAssertEqual(m.polygons.count, 0)
        XCTAssertEqual(m.polygonPoints.count, 0)
    }
}
