import XCTest
@testable import WoundMeasurementApp

/// 對齊 SSOT 組織金標(engineering/generated/tissue_golden.json)；三端分類須一致。
final class TissueClassifierV2Tests: XCTestCase {
    func testGoldenSamples() {
        let cases: [([Int], Int)] = [
            ([105, 22, 30], 3),   // 暗紅墨水→肉芽
            ([35, 33, 30], 1),    // 暗低飽和→壞死
            ([200, 170, 40], 2),  // 黃→腐肉
            ([235, 200, 205], 4), // 淡粉→上皮
            ([190, 40, 45], 3),   // 紅→肉芽
            ([150, 150, 150], 5), // 灰→其他
            ([60, 55, 50], 1)     // 暗灰→壞死
        ]
        for (rgb, expected) in cases {
            XCTAssertEqual(TissueClassifierV2.classifyPixel(rgb[0], rgb[1], rgb[2]), expected)
        }
    }
    func testHsvMatchesOpenCV() {
        let hsv = TissueClassifierV2.rgb2hsv(200, 170, 40)
        XCTAssertEqual(hsv.v, 200)
        XCTAssertTrue((22...26).contains(hsv.h))
        XCTAssertTrue((200...206).contains(hsv.s))
    }
}
