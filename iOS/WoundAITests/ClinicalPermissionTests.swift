import XCTest
@testable import WoundMeasurementApp

final class ClinicalPermissionTests: XCTestCase {
    private func identity(_ role: String, _ perms: Set<String>) -> LoginIdentity {
        LoginIdentity(identity: "demo:test", org: "demo", user: "test", role: role,
                      roleZh: role, displayName: nil, perms: perms)
    }

    @MainActor
    private func edit(_ vm: MeasureViewModel) {
        let polygon = [[0, 0], [1, 0], [1, 1]]
        let raster = EditRaster(mask: [1], tissue: [1], origMask: [1], rx0: 0, ry0: 0,
                                mw: 1, mh: 1, mScale: 1, cm2PerPx: 1)
        vm.applyEdited(polygon: polygon, all: [polygon], iou: 1, newArea: 1,
                       tissue: [:], raster: raster)
    }

    @MainActor
    func testNurseCanEditWithoutBeingMarkedPhysicianVerified() {
        let vm = MeasureViewModel()
        vm.identity = identity("nurse", ["measure.clinical", "record.save"])
        edit(vm)
        XCTAssertNotNil(vm.editedPolygons)
        XCTAssertFalse(vm.doctorVerified)
        XCTAssertNotNil(vm.submitBlockedReason)
    }

    @MainActor
    func testRoleLabelAloneDoesNotGrantVerification() {
        let vm = MeasureViewModel()
        vm.identity = identity("physician", [])
        edit(vm)
        XCTAssertFalse(vm.doctorVerified)
    }

    @MainActor
    func testNurseReEditClearsPreviousPhysicianVerification() {
        let vm = MeasureViewModel()
        vm.identity = identity("physician", ["gt.verify", "annotation.submit"])
        edit(vm)
        XCTAssertTrue(vm.doctorVerified)
        vm.identity = identity("nurse", ["record.save"])
        edit(vm)
        XCTAssertFalse(vm.doctorVerified)
    }

    @MainActor
    func testMissingIdentityDoesNotVerifyAnEdit() {
        let vm = MeasureViewModel()
        edit(vm)
        XCTAssertFalse(vm.doctorVerified)
    }

    func testTrainingRequiresBothCapabilities() {
        XCTAssertFalse(identity("physician", ["gt.verify"]).canSubmitClinicalTraining)
        XCTAssertFalse(identity("physician", ["annotation.submit"]).canSubmitClinicalTraining)
        XCTAssertTrue(identity("physician", ["gt.verify", "annotation.submit"]).canSubmitClinicalTraining)
    }
}
