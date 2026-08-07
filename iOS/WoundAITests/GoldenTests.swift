import XCTest
@testable import WoundMeasurementApp

/**
 金標測試：計分與組織分型必須與後端逐值相同。

 金標來源 `engineering/generated/push_golden.json`、`tissue_golden.json`——同一份檔案
 Android 與後端也在用。三端跑同一組數字，才談得上「同一張照片在哪支手機上都一樣」。

 這裡把金標內嵌成常數，而不是在執行期讀 JSON：測試 bundle 要打包 SSOT 檔會多一層設定，
 而數值本身很少變。SSOT 一改，`gen_preprocessing_constants.py` 會同時更新
 `Preprocessing.generated.swift`，屆時這裡若沒跟上就會紅——那正是我們要的訊號。
 */
final class PushGoldenTests: XCTestCase {

    /// `push_golden.json` → `area_subscore`
    private let areaGolden: [(Double, Int)] = [
        (0, 0), (0.2, 1), (0.5, 2), (0.9, 3), (1.5, 4),
        (2.5, 5), (3.5, 6), (6, 7), (10, 8), (20, 9), (30, 10)
    ]

    func testAreaSubscoreMatchesGolden() {
        for (cm2, expected) in areaGolden {
            XCTAssertEqual(WoundPipeline.areaSubscore(cm2), expected,
                           "面積 \(cm2) cm² 應得 \(expected) 分")
        }
    }

    /// 未校正 → nil，**不是 0**。0 的意思是「已癒合」。
    func testNilAreaGivesNilSubscore() {
        XCTAssertNil(WoundPipeline.areaSubscore(nil))
        XCTAssertEqual(WoundPipeline.areaSubscore(0.0), 0)
    }

    /// `push_golden.json` → `tissue_subscore`：取最差的存在組織，門檻 5%。
    func testTissueSubscoreMatchesGolden() {
        XCTAssertEqual(WoundPipeline.tissueSubscore(["necrosis": 0.1, "granulation": 0.8]), 4)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["slough": 0.2, "granulation": 0.7]), 3)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["granulation": 0.95]), 2)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["epithelial": 0.9]), 1)
        XCTAssertEqual(WoundPipeline.tissueSubscore([:]), 0)
    }

    /// 門檻邊界：4.9% 的壞死不算「存在」，5.0% 算。
    func testTissuePresenceThreshold() {
        XCTAssertEqual(WoundPipeline.tissueSubscore(["necrosis": 0.049, "granulation": 0.9]), 2)
        XCTAssertEqual(WoundPipeline.tissueSubscore(["necrosis": 0.05, "granulation": 0.9]), 4)
    }

    /// `push_golden.json` → `push_cases`
    func testPushCasesMatchGolden() {
        let necrosis = ["necrosis": 0.1, "granulation": 0.8]
        let slough   = ["slough": 0.2, "granulation": 0.7]
        let granul   = ["granulation": 0.95]

        var p = WoundPipeline.push(cm2: 8.66, frac: necrosis, exudate: 2)
        XCTAssertEqual(p.area, 8); XCTAssertEqual(p.tissue, 4)
        XCTAssertEqual(p.partial, 12); XCTAssertEqual(p.full, 14)

        p = WoundPipeline.push(cm2: 2.78, frac: slough, exudate: 2)
        XCTAssertEqual(p.area, 5); XCTAssertEqual(p.tissue, 3)
        XCTAssertEqual(p.partial, 8); XCTAssertEqual(p.full, 10)

        p = WoundPipeline.push(cm2: 0.0, frac: granul, exudate: nil)
        XCTAssertEqual(p.area, 0); XCTAssertEqual(p.tissue, 2)
        XCTAssertEqual(p.partial, 2); XCTAssertNil(p.full)
    }

    /// 面積未知時 partial 也是 nil——半個 PUSH 分數比沒有分數更容易誤導。
    func testUncalibratedGivesNilPartial() {
        let p = WoundPipeline.push(cm2: nil, frac: ["granulation": 0.9], exudate: 2)
        XCTAssertNil(p.area); XCTAssertNil(p.partial); XCTAssertNil(p.full)
        XCTAssertEqual(p.tissue, 2)
    }
}

final class TissueGoldenTests: XCTestCase {

    /// `tissue_golden.json` → `samples`。碼為**分類器碼**（1 壞死 / 3 肉芽）。
    private let samples: [(r: Int, g: Int, b: Int, code: Int, note: String)] = [
        (105, 22, 30, 3, "暗紅墨水→肉芽"),
        (35, 33, 30, 1, "暗低飽和→壞死"),
        (200, 170, 40, 2, "黃→腐肉"),
        (235, 200, 205, 4, "淡粉→上皮"),
        (190, 40, 45, 3, "紅→肉芽"),
        (150, 150, 150, 5, "灰→其他"),
        (60, 55, 50, 1, "暗灰→壞死")
    ]

    func testClassifyPixelMatchesGolden() {
        for s in samples {
            XCTAssertEqual(TissueClassifierV2.classifyPixel(s.r, s.g, s.b), s.code,
                           "RGB(\(s.r),\(s.g),\(s.b)) \(s.note)")
        }
    }

    /// HSV 必須是 OpenCV 8-bit 公式（H 0–180），否則所有色相門檻都會差一倍。
    func testHSVIsOpenCVEightBit() {
        let red = TissueClassifierV2.rgb2hsv(255, 0, 0)
        XCTAssertEqual(red.h, 0); XCTAssertEqual(red.s, 255); XCTAssertEqual(red.v, 255)

        let green = TissueClassifierV2.rgb2hsv(0, 255, 0)
        XCTAssertEqual(green.h, 60)     // OpenCV 的 120° / 2

        let blue = TissueClassifierV2.rgb2hsv(0, 0, 255)
        XCTAssertEqual(blue.h, 120)     // OpenCV 的 240° / 2

        let grey = TissueClassifierV2.rgb2hsv(128, 128, 128)
        XCTAssertEqual(grey.s, 0); XCTAssertEqual(grey.v, 128)
    }
}

// MARK: - 面積

final class AreaTests: XCTestCase {

    /// 比例法：`woundPx × markerMm² / markerPxArea / 100`。
    func testAreaByRatio() {
        // marker 12mm、影像上 100×100 px → 每 px 邊長 0.12mm → 每 px 0.000144 cm²
        let a = WoundPipeline.areaCm2ByRatio(woundPx: 10_000, markerPxArea: 10_000, markerMm: 12.0)
        XCTAssertEqual(a!, 1.44, accuracy: 1e-9)
    }

    func testAreaByRatioRejectsZeroMarker() {
        XCTAssertNil(WoundPipeline.areaCm2ByRatio(woundPx: 100, markerPxArea: 0))
    }

    /// 修邊後的面積走 `mm_per_px`，**不是**「AI 面積 × 修正比例」。
    func testAreaFromMmPerPx() {
        // 0.2 mm/px → 每 px 0.04 mm² → 10,000 px ＝ 400 mm² ＝ 4 cm²
        XCTAssertEqual(WoundPipeline.areaCm2(pixelCount: 10_000, mmPerPx: 0.2)!,
                       4.0, accuracy: 1e-9)
        XCTAssertNil(WoundPipeline.areaCm2(pixelCount: 10_000, mmPerPx: nil))
    }

    /// 面積冪等：柵格原樣載回 → 像素數不變 → 面積不變。
    func testRasterAreaIsIdempotent() {
        var r = EditRaster(mask: [1, 1, 0, 1], tissue: [1, 1, 0, 3], origMask: [1, 1, 1, 1],
                           rx0: 0, ry0: 0, mw: 2, mh: 2, mScale: 1, cm2PerPx: 0.25)
        r.maskPx = 3
        XCTAssertEqual(r.areaCm2!, 0.75, accuracy: 1e-9)
        XCTAssertEqual(r.correctionIou!, 0.75, accuracy: 1e-9)   // 3 交集 / 4 聯集
    }

    /// 柵格 → 影像座標的還原公式，必須與後端 `_raster_rect()` 相同。
    func testRasterRectMatchesBackendFormula() {
        let r = EditRaster(mask: [], tissue: [], origMask: [],
                           rx0: 300, ry0: 120, mw: 400, mh: 200, mScale: 2, cm2PerPx: nil)
        let rect = r.imageRect
        XCTAssertEqual(rect.origin.x, 300, accuracy: 1e-9)
        XCTAssertEqual(rect.width, 200, accuracy: 1e-9)    // mw / m_scale
        XCTAssertEqual(rect.height, 100, accuracy: 1e-9)
    }
}

// MARK: - 同意閘門

final class ConsentGateTests: XCTestCase {

    func testTrainEffectiveRequiresNoWithdrawal() {
        var c = Consent(patientId: "p", consentCare: true, consentTrain: true)
        XCTAssertTrue(c.trainEffective)
        c.withdrawnAt = Date()
        XCTAssertFalse(c.trainEffective, "已撤回就不再有效，即使 consentTrain 仍為 true")
    }

    /// WD 代碼格式必須符合後端 `^WD-[A-Za-z0-9_-]{1,32}$`。
    func testWdCodeMatchesBackendRegex() {
        let re = try! NSRegularExpression(pattern: "^WD-[A-Za-z0-9_-]{1,32}$")
        for _ in 0..<200 {
            let code = WoundCase.newWdCode()
            let n = re.numberOfMatches(in: code, range: NSRange(code.startIndex..., in: code))
            XCTAssertEqual(n, 1, "代碼 \(code) 不符後端白名單")
        }
    }

    /// 代碼必須夠隨機——時間戳尾碼會在同一毫秒內碰撞，而且回診時拿到新碼會斷開時間軸。
    func testWdCodesAreUnique() {
        let codes = Set((0..<500).map { _ in WoundCase.newWdCode() })
        XCTAssertEqual(codes.count, 500)
    }
}
