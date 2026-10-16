import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

final class MedicalWoundProfileTests: XCTestCase {
    func testSharedGoldenCasesAndRoundTrip() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let data = try Data(contentsOf: root.appendingPathComponent("engineering/contracts/medical_wound_profile_cases.json"))
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String:Any]])
        for c in cases {
            let raw = try JSONSerialization.data(withJSONObject: c["profile"]!,options:[.fragmentsAllowed])
            let p = MedicalWoundProfile.decode(String(decoding:raw,as:UTF8.self))
            XCTAssertEqual(p != nil,c["valid"] as? Bool,c["name"] as? String ?? "case")
            if let p { XCTAssertEqual(MedicalWoundProfile.decode(try p.encoded()),p) }
        }
    }
    func testNoAutomaticDiagnosisOrScore() {
        var p = MedicalWoundProfile();p.site="heel";p.side="right";p.surface="plantar";p.etiology="pressure";p.factors=["diabetes"]
        XCTAssertTrue(p.valid)
        XCTAssertEqual(MedicalWoundCatalog.shared.guidance(etiology:p.etiology).map(\.code),["pressure_stage","push"])
        XCTAssertTrue(MedicalWoundCatalog.shared.guidance(etiology:"unknown").isEmpty)
        XCTAssertTrue(MedicalWoundCatalog.shared.instruments.allSatisfy{$0.status=="requirements_only"})
        XCTAssertThrowsError(try MedicalWoundProfile().encoded())
    }

    func testReceiptRejectsMissingOrDifferentProfile() throws {
        var p = MedicalWoundProfile();p.site="heel";p.side="right";p.surface="plantar";p.etiology="pressure"
        let raw = try p.encoded()
        var body: [String:Any] = ["status":"enqueued","code":"WD-test","image_id":"image-test","annotation_receipt_id":"0123456789abcdef"]
        func receipt() throws -> AnnotationOutcome {
            try BackendClient.annotationReceipt(JSONSerialization.data(withJSONObject:body),code:"WD-test",imageID:"image-test",woundProfileJson:raw)
        }
        XCTAssertThrowsError(try receipt())
        body["wound_profile"] = try JSONSerialization.jsonObject(with:Data(raw.utf8))
        XCTAssertNoThrow(try receipt())
        p.side="left"
        body["wound_profile"] = try JSONSerialization.jsonObject(with:Data(p.encoded().utf8))
        XCTAssertThrowsError(try receipt())
    }
}
