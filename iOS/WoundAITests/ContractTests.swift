import XCTest
@testable import WoundMeasurementApp

/**
 後端契約一致性測試。

 ## 為什麼這組測試存在

 這個專案反覆出現同一種缺陷，最近一次記錄在 `COMMIT_MSG.txt`：

 > 兩者都是「**後端做對了，App 沒接上**」：
 > `/api/v1/consent/restore` 存在 → App 沒呼叫；`tissue_raster` 存在且欄位完整 → 預覽沒使用。
 > 契約定得好不等於契約被履行。

 這些缺陷全都不會產生執行期錯誤：服務回 200，畫面顯示一個合理的數字，只有資料是錯的。
 唯一抓得到的方式，是把「回應裡的每一個欄位都必須被讀進來」寫成測試。

 fixture 依 `Backend/Flask/app.py` 的 classify 回傳結構逐欄位建構。
 後端改欄位時這裡會紅——那正是我們要的訊號。
 */
final class ClassifyContractTests: XCTestCase {

    /// 完整回應（有 ArUco、有色卡、已 escalate）。
    private let fullResponse = """
    {
      "image_id": "aaaabbbbccccdddd",
      "image_w": 2048,
      "image_h": 1536,
      "image_reused": false,
      "stage2_segment": {
        "model": "ensemble.AU",
        "wound_ratio": 0.077,
        "confidence": 0.83,
        "route": "cloud_escalated(AU)",
        "escalated": true,
        "au_area_ratio": 1.62,
        "iou_student_au": 0.41,
        "wound_polygon": [[100,100],[400,120],[380,500],[90,470]]
      },
      "stage3_calibrate": {
        "method": "aruco(marker 12.0mm)",
        "area_cm2": 8.07,
        "mm_per_px": 0.2034,
        "marker_quad": [[10,10],[110,12],[112,112],[8,110]],
        "marker_id": 7,
        "marker_mm": 12.0,
        "note": null
      },
      "stage3b_colorcal": {
        "ok": true, "reason": "", "gain_b": 1.02341, "gain_g": 1.0, "gain_r": 0.9812,
        "exposure": 1.0412, "illuminant_bgr": [180.2,185.4,190.1], "cast": 0.0412,
        "clipped_frac": 0.0, "hue_err": {"R":2.1,"G":-3.4,"B":1.0,"Y":0.5},
        "method": "vonkries_neutral_v1"
      },
      "stage4_tissue": {
        "method": "v2(色卡WB+HSV)",
        "tissue_frac": {"necrosis":0.08,"slough":0.14,"granulation":0.78,
                        "epithelial":0.0,"other":0.0},
        "note": null
      },
      "stage5_severity": {
        "tool": "PUSH (NPUAP 3.0)", "area_subscore": 7, "tissue_subscore": 2,
        "exudate_subscore": null, "total_partial_img": 9, "total_full": null,
        "range_full": "0-17(低=癒合)"
      },
      "quality": {"focus_lapvar":152.3,"clipped_frac":0.0031,"roi_short_px":612,
                  "marker_side_px":96.4,"marker_frac":0.0471,"marker_skew":0.082},
      "phantom_mode": false, "phantom_pass": null, "phantom_hint": false,
      "disclaimer": "輔助用途、非診斷、需醫師確認"
    }
    """

    private func parse(_ s: String) -> ClassifyResult {
        return BackendClient.parseClassify(JSONAny(data: Data(s.utf8))!)
    }

    func testEveryFieldIsRead() {
        let r = parse(fullResponse)

        // 飛輪資料鏈——缺任何一項都會產生後端無法訓練的孤兒 GT
        XCTAssertEqual(r.imageId, "aaaabbbbccccdddd")
        XCTAssertEqual(r.imageW, 2048)
        XCTAssertEqual(r.imageH, 1536)
        XCTAssertFalse(r.imageReused)

        // 分割
        XCTAssertEqual(r.segModel, "ensemble.AU")
        XCTAssertEqual(r.route, "cloud_escalated(AU)")
        XCTAssertTrue(r.escalated)
        XCTAssertEqual(r.confidence, 0.83, accuracy: 1e-9)
        XCTAssertEqual(r.woundPolygon.count, 4)
        XCTAssertEqual(r.woundPolygon[0], [100, 100])

        // 校正——marker_quad 是目視複核的唯一依據，必須完整帶到 UI
        XCTAssertEqual(r.areaCm2!, 8.07, accuracy: 1e-9)
        XCTAssertEqual(r.mmPerPx!, 0.2034, accuracy: 1e-9)
        XCTAssertEqual(r.markerQuad?.count, 4)
        XCTAssertEqual(r.markerMm!, 12.0, accuracy: 1e-9)
        XCTAssertEqual(r.calibMethod, "aruco(marker 12.0mm)")

        // 色準：增益必須**乘上曝光係數**才是端上該用的那一組
        XCTAssertNotNil(r.wbGains)
        XCTAssertEqual(r.wbGains![0], 0.9812 * 1.0412, accuracy: 1e-9)
        XCTAssertEqual(r.wbGains![1], 1.0 * 1.0412, accuracy: 1e-9)
        XCTAssertEqual(r.wbGains![2], 1.02341 * 1.0412, accuracy: 1e-9)

        // 組織：恰好五類
        XCTAssertEqual(Set(r.tissueFrac.keys),
                       ["necrosis", "slough", "granulation", "epithelial", "other"])
        XCTAssertEqual(r.tissueFrac["granulation"]!, 0.78, accuracy: 1e-9)
        XCTAssertFalse(r.usedGrayWorldWB)

        // PUSH：total_full 恆為 null（滲液須醫師輸入）
        XCTAssertEqual(r.pushPartial, 9)
        XCTAssertNil(r.pushFull)

        // 品質指標
        XCTAssertEqual(r.quality["marker_skew"]!, 0.082, accuracy: 1e-9)
        XCTAssertEqual(r.quality.count, 6)
    }

    /// `image_id: null` ＝ 已撤回同意或存檔失敗。**必須解析成 nil 並封鎖送出。**
    func testNullImageIdBlocksSubmission() {
        let s = fullResponse.replacingOccurrences(
            of: "\"image_id\": \"aaaabbbbccccdddd\"", with: "\"image_id\": null")
        XCTAssertNil(parse(s).imageId)
    }

    /**
     舊版後端沒有 `stage3b_colorcal`。App 必須照常運作，而不是整支 classify 拋例外。

     這是 Android `BackendClient.kt` 明確記錄的決定（用 `optJSONObject` 而非 `getJSONObject`）。
     */
    func testMissingColorCalDoesNotBreakParsing() {
        var s = fullResponse
        if let a = s.range(of: "\"stage3b_colorcal\""),
           let b = s.range(of: "\"stage4_tissue\"") {
            s.replaceSubrange(a.lowerBound..<b.lowerBound, with: "")
        }
        let r = parse(s)
        XCTAssertNil(r.wbGains)
        XCTAssertEqual(r.areaCm2!, 8.07, accuracy: 1e-9)   // 其餘欄位不受影響
    }

    /// 沒有 ArUco 時：面積為 nil（**不是 0**），且組織退回 gray-world 要看得出來。
    func testUncalibratedResponse() {
        let s = """
        {
          "image_id": "0123456789abcdef", "image_w": 800, "image_h": 600, "image_reused": true,
          "stage2_segment": {"model":"student","confidence":0.6,"route":"student",
                             "escalated":false,"wound_polygon":[]},
          "stage3_calibrate": {"method":"none","area_cm2":null,"mm_per_px":null,
                               "marker_quad":null,"marker_id":null,"marker_mm":null,
                               "note":"未校正(無 ArUco 且未提供 cm_per_pixel)"},
          "stage3b_colorcal": {"ok": false, "reason": "無 ArUco,無法定位色卡"},
          "stage4_tissue": {"method":"v2(gray-world WB+HSV)",
                            "tissue_frac":{"necrosis":0,"slough":0,"granulation":0.2,
                                           "epithelial":0,"other":0.8}},
          "stage5_severity": {"area_subscore":null,"tissue_subscore":2,
                              "total_partial_img":null,"total_full":null},
          "phantom_hint": true
        }
        """
        let r = parse(s)
        XCTAssertNil(r.areaCm2)
        XCTAssertNil(r.markerQuad)
        XCTAssertNil(r.wbGains)
        XCTAssertTrue(r.usedGrayWorldWB)
        XCTAssertTrue(r.phantomHint)
        XCTAssertTrue(r.imageReused)
        XCTAssertNil(r.pushPartial)
    }

    /// 提示訊息必須涵蓋每一種「服務回 200 但數字可能是錯的」的情形。
    ///
    /// `MeasureViewModel` 標了 `@MainActor`，而全域 actor 標在**型別**上時連 static 成員
    /// 都會被隔離（SE-0316）。同步的測試方法不在 main actor 上，所以這裡要補標。
    @MainActor
    func testAdvisoriesCoverSilentFailureModes() {
        let r = parse("""
        {
          "image_id": null, "image_w": 10, "image_h": 10, "image_reused": true,
          "stage2_segment": {"confidence":0.4,"route":"student","wound_polygon":[]},
          "stage3_calibrate": {"method":"none","area_cm2":null,"marker_quad":null},
          "stage4_tissue": {"method":"v2(gray-world WB+HSV)","tissue_frac":{}},
          "stage5_severity": {}, "phantom_hint": true
        }
        """)
        let a = MeasureViewModel.advisories(r, clinical: true)
        XCTAssertTrue(a.contains { $0.contains("image_id") })
        XCTAssertTrue(a.contains { $0.contains("相同的影像") })
        XCTAssertTrue(a.contains { $0.contains("校正貼紙") })
        XCTAssertTrue(a.contains { $0.contains("gray-world") })
        XCTAssertTrue(a.contains { $0.contains("空遮罩") })
        XCTAssertTrue(a.contains { $0.contains("信心度") })
    }
}

// MARK: - 組織碼契約

/**
 兩套組織碼、只轉一次、方向單一。弄反的話畫面看起來完全正常，只有訓練 GT 是錯的
 ——而壞死與肉芽恰好是臨床後果最相反的兩類。這組測試把它釘死。
 */
final class TissueCodeContractTests: XCTestCase {

    /// 資料集正典 ＝ **修邊畫面碼**。與 `train_tissue_seg.py` 的 `CLASSES` 對齊。
    func testEditCodeIsDatasetCanonical() {
        XCTAssertEqual(TissueCode.editNames, ["", "肉芽", "腐肉", "壞死", "上皮", "其他"])
        XCTAssertEqual(TissueCode.editToKey,
                       ["", "granulation", "slough", "necrosis", "epithelial", "other"])
    }

    /// 分類器碼 → 修邊碼：1↔3 互換，其餘不動。
    func testClassifierToEditMapping() {
        XCTAssertEqual(TissueSeg.clsToEdit, [0, 3, 2, 1, 4, 5])
        // 分類器的「壞死」(1) 必須落到修邊碼的「壞死」(3)
        let necrosisEdit = Int(TissueSeg.clsToEdit[1])
        XCTAssertEqual(TissueCode.editToKey[necrosisEdit], "necrosis")
        // 分類器的「肉芽」(3) 必須落到修邊碼的「肉芽」(1)
        let granulationEdit = Int(TissueSeg.clsToEdit[3])
        XCTAssertEqual(TissueCode.editToKey[granulationEdit], "granulation")
    }

    /// 上傳前**不可**再轉一次——雙重轉換會把 1 和 3 換回去。
    func testNoSecondConversionOnExport() {
        var raster = EditRaster(mask: [1, 1, 1, 1], tissue: [1, 1, 3, 3], origMask: [1, 1, 1, 1],
                               rx0: 0, ry0: 0, mw: 2, mh: 2, mScale: 1, cm2PerPx: nil)
        raster.maskPx = 4
        let frac = raster.tissueFrac()
        XCTAssertEqual(frac["granulation"]!, 0.5, accuracy: 1e-9)   // 碼 1
        XCTAssertEqual(frac["necrosis"]!, 0.5, accuracy: 1e-9)      // 碼 3
    }
}
