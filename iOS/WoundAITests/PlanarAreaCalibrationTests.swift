import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class PlanarAreaCalibrationTests: XCTestCase {
    private func map(_ status: String = "accepted") -> PlanarAreaCalibration {
        PlanarAreaCalibration(version:1,status:status,reason:"test",h:[1,0,0,0,1,0,0,0,1],bounds:[-1,-1,200,200])
    }
    func testHomographySignAndDisjointHorizon() throws {
        var c=map();c.h=[1,0,0,0,1,0,0.002,0,1]
        let m=[UInt8](repeating:1,count:100)
        let expected=try XCTUnwrap(c.area(mask:m,width:10,height:10,x:0,y:0,scale:1))
        c.h=c.h.map { -$0 }
        XCTAssertEqual(try XCTUnwrap(c.area(mask:m,width:10,height:10,x:0,y:0,scale:1)),expected,accuracy:1e-12)
        c.h=[1,0,0,0,1,0,1,0,-10]
        var separated=[UInt8](repeating:0,count:40)
        for row in 0..<2 { for col in [0,1,18,19] { separated[row*20+col]=1 } }
        XCTAssertNil(c.area(mask:separated,width:20,height:2,x:0,y:0,scale:1))
    }
    func testProvisionalAreaSurvivesWithExplicitWarning() throws {
        var c=map("provisional")
        c.reason="corner_extrapolation_unstable";c.area_quality="unverified"
        c.quality_reasons=["sensitivity_threshold_exceeded"]
        XCTAssertTrue(c.qualityMessage.contains("面積暫估"))
        XCTAssertEqual(try XCTUnwrap(c.area(mask:[UInt8](repeating:1,count:100),width:10,height:10,x:0,y:0,scale:1)),1,accuracy:1e-12)
        XCTAssertEqual(PlanarAreaCalibration.parse(c.json),c)
        c.quality_reasons=[]
        XCTAssertTrue(c.qualityMessage.contains("資料不完整"))
    }
    func testPixelAreaHolesAndTranslation() throws {
        var mask=[UInt8](repeating:1,count:100);mask[50]=0
        XCTAssertEqual(try XCTUnwrap(map().area(mask:mask,width:10,height:10,x:0,y:0,scale:1)),0.99,accuracy:1e-12)
        XCTAssertEqual(try XCTUnwrap(map().area(mask:mask,width:10,height:10,x:50,y:50,scale:1)),0.99,accuracy:1e-12)
        XCTAssertEqual(try XCTUnwrap(map().area(mask:mask,width:10,height:10,x:50,y:50,scale:2)),0.2475,accuracy:1e-12)
    }
    func testPerspectiveUsesPixelPosition() throws {
        var c=map();c.h=[1,0,0,0,1,0,0.002,0,1]
        let m=[UInt8](repeating:1,count:100)
        let a=try XCTUnwrap(c.area(mask:m,width:10,height:10,x:0,y:0,scale:1))
        let b=try XCTUnwrap(c.area(mask:m,width:10,height:10,x:80,y:0,scale:1))
        XCTAssertGreaterThan(a,b*1.4)
        // Independent analytic integral of det(H)/(1+0.002*x)^3 over the rectangle.
        let expected=10/(0.004)*(1/pow(1-0.001,2)-1/pow(1+0.019,2))/100
        XCTAssertEqual(a,expected,accuracy:1e-10)
    }
    func testRejectedOutsideDomainAndHorizonDoNotFallback() {
        XCTAssertNil(PlanarAreaCalibration.parse(nil))
        XCTAssertEqual(PlanarAreaCalibration.parse("invalid")?.status,"rejected")
        XCTAssertEqual(PlanarAreaCalibration.parse(map().json),map())
        let m=[UInt8](repeating:1,count:100)
        XCTAssertNil(map("rejected").area(mask:m,width:10,height:10,x:0,y:0,scale:1))
        XCTAssertNil(map().area(mask:m,width:10,height:10,x:198,y:0,scale:1))
        var c=map();c.h=[1,0,0,0,1,0,-1,0,1]
        XCTAssertNil(c.area(mask:m,width:10,height:10,x:0,y:0,scale:1))
    }
    func testBoardMetadataSurvivesRoundTrip() throws {
        var c=map();c.profile="woundai-a4-coplanar-v1";c.marker_ids=[40,41,42,43]
        c.reprojection_rms_px=0.25;c.holdout_max_px=1.2
        c.quality_policy="marker-relative-v2";c.reference_marker_edge_px=100;c.marker_edge_px=120
        c.reprojection_max_normalized_px=0.4;c.holdout_max_normalized_px=1.0
        XCTAssertEqual(PlanarAreaCalibration.parse(c.json),c)
        XCTAssertEqual(try JSONDecoder().decode(PlanarAreaCalibration.self,from:JSONEncoder().encode(c)),c)
        XCTAssertEqual(try XCTUnwrap(c.area(mask:[UInt8](repeating:1,count:100),width:10,height:10,x:50,y:50,scale:1)),1,accuracy:1e-12)
    }
    func testRenderedBoardFixtureMatchesIndependentQuadrilateral() throws {
        let data=Data(#"{"version":1,"status":"accepted","reason":"coplanarity_requires_physical_confirmation","h":[0.44098353031294957,-2.5253139731583973e-05,-231.37025856097148,0.030974577620002584,0.36692584101495146,-157.53372868640912,0.0003814012035134621,-5.105655169165877e-07,1.0],"bounds":[660.5,484.5,924.5,832.5],"profile":"woundai-a4-coplanar-v1","marker_ids":[40,41,42,43],"corner_error_px":0.5,"relative_sensitivity":0.005280065743657247,"reprojection_rms_px":0.2867138174581993,"holdout_max_px":1.8919458503471254}"#.utf8)
        let c=try JSONDecoder().decode(PlanarAreaCalibration.self,from:data)
        XCTAssertEqual(c.marker_ids,[40,41,42,43])
        XCTAssertEqual(try XCTUnwrap(c.area(mask:[UInt8](repeating:1,count:2000),width:50,height:40,x:750,y:600,scale:1)),1.7879024393013423,accuracy:1e-10)
    }
    func testPhysicalReplayCropAndFailedHoldoutRemainSeparated() throws {
        // A4 physical frame 02, native RGB pixel centres; independent whole-quad reference.
        var c=PlanarAreaCalibration(version:1,status:"accepted",reason:"coplanarity_requires_physical_confirmation",
            h:[0.1629282410624262,0.0036057974347594828,-157.79072118031766,-0.003512176309280293,0.1678439890493142,-292.3200659017753,-4.6446639645521604e-05,4.095420965172934e-05,1],
            bounds:[1221.5,1988.5,1730.5,2581.5])
        let m=[UInt8](repeating:1,count:2000)
        XCTAssertEqual(try XCTUnwrap(c.area(mask:m,width:50,height:40,x:1400,y:2000,scale:1)),0.5359835719698753,accuracy:1e-10)
        c.status="rejected";c.reason="board_holdout_failed"
        // Keeping an old H must not bypass a new failed quality result.
        XCTAssertNil(c.area(mask:m,width:50,height:40,x:1400,y:2000,scale:1))
    }
    func testQualityGuidanceDistinguishesCausesAndInvalidContracts() {
        var c=map("rejected")
        for (reason,fragment) in [("marker_too_small","靠近"),("corner_extrapolation_unstable","貼近傷口"),
                                  ("board_holdout_failed","紙張平貼硬板"),("four_unique_board_markers_required","四個黑白碼"),
                                  ("outside_coplanar_region","超出"),("empty_mask","遮罩"),("unexpected_reason","未能可靠") ] {
            c.reason=reason;XCTAssertTrue(c.qualityMessage.contains(fragment))
            XCTAssertNil(c.area(mask:[1],width:1,height:1,x:0,y:0,scale:1))
        }
        c=map();XCTAssertTrue(c.qualityMessage.contains("不代表三維"))
        c.version=2;XCTAssertTrue(c.qualityMessage.contains("相容"))
        c=map();c.h=[];XCTAssertTrue(c.qualityMessage.contains("未能可靠"))
        c=map("unknown");XCTAssertTrue(c.qualityMessage.contains("未能可靠"))
    }
    func testCandidateProfileAndMixedStickers() throws {
        var c=map();c.profile="woundai-sticker-20-candidate-v1";c.marker_ids=[20]
        XCTAssertEqual(PlanarAreaCalibration.parse(c.json),c)
        XCTAssertTrue(c.qualityMessage.contains("20 mm 研究候選"))
        c.status="rejected";c.reason="multiple_calibration_markers"
        XCTAssertTrue(c.qualityMessage.contains("只保留"))
        XCTAssertNil(c.area(mask:[1],width:1,height:1,x:0,y:0,scale:1))
        c.reason="unknown_sticker_profile";XCTAssertTrue(c.qualityMessage.contains("勿自行猜"))
    }
}
