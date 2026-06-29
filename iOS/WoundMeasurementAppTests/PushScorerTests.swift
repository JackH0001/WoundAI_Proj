import XCTest
@testable import WoundMeasurementApp

/// 對齊 SSOT 金標(engineering/generated/push_golden.json)；三端計分須一致(差異=0)。
final class PushScorerTests: XCTestCase {
    func testAreaSubscoreBands() {
        let areas: [Double] = [0.0, 0.2, 0.5, 0.9, 1.5, 2.5, 3.5, 6.0, 10.0, 20.0, 30.0]
        let expected = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        for i in areas.indices { XCTAssertEqual(WoundPipeline.areaSubscore(areas[i]), expected[i]) }
    }
    func testTissueWorst() {
        XCTAssertEqual(WoundPipeline.tissueSubscore(["necrosis": 0.1, "granulation": 0.8]), 4)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["granulation": 0.95]), 2)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["granulation": 0.02]), 0)
    }
    func testPushGolden() {
        let p1 = WoundPipeline.push(8.66, ["granulation": 0.78, "slough": 0.14, "necrosis": 0.08], 2)
        XCTAssertEqual(p1.partial, 12); XCTAssertEqual(p1.full, 14)
        let p2 = WoundPipeline.push(2.78, ["slough": 0.5, "granulation": 0.4], 2)
        XCTAssertEqual(p2.partial, 8); XCTAssertEqual(p2.full, 10)
        let p3 = WoundPipeline.push(0.0, ["granulation": 1.0], nil)
        XCTAssertEqual(p3.partial, 2); XCTAssertNil(p3.full)
    }
}
