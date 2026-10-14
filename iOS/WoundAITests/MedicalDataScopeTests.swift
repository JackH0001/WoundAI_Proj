import XCTest
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

@MainActor
final class MedicalDataScopeTests: XCTestCase {
    private var root: URL!
    override func setUp() {
        super.setUp()
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }
    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        super.tearDown()
    }
    private func capture(_ value: Float) -> DepthCapture {
        DepthCapture(map: [value, value + 0.01, value + 0.02, value + 0.03], width: 2, height: 2,
                     fx: 2, fy: 2, cx: 0.5, cy: 0.5, refWidth: 2, refHeight: 2,
                     accuracy: "absolute", filtered: false, sourceExifOrientation: 6)
    }
    func testLegacyPathsAreUnchangedAndReviewPathsAreDistinct() throws {
        XCTAssertEqual(MedicalDataScope.mmh.database(under: root), root.appendingPathComponent("wound_measurement_database.sqlite"))
        XCTAssertEqual(MedicalDataScope.mmh.depthIndex(under: root), root.appendingPathComponent("depth_index.json"))
        XCTAssertEqual(MedicalDataScope.mmh.images(under: root), root.appendingPathComponent("wound_images", isDirectory: true))
        XCTAssertEqual(MedicalDataScope.mmh.settingsSuite, "woundai_settings")
        XCTAssertNotEqual(MedicalDataScope.mmh.settingsSuite, MedicalDataScope.syntheticReview.settingsSuite)
        XCTAssertNotEqual(MedicalDataScope.mmh.exports(under: root), MedicalDataScope.syntheticReview.exports(under: root))
        let mmh = try MedicalLocalStores(scope: .mmh, support: root)
        let review = try MedicalLocalStores(scope: .syntheticReview, support: root)
        XCTAssertNotEqual(mmh.images.depthIndexURL, review.images.depthIndexURL)
    }
    func testSameRecordIDDoesNotCrossDatabases() async throws {
        let mmh = try MedicalLocalStores(scope: .mmh, support: root)
        let review = try MedicalLocalStores(scope: .syntheticReview, support: root)
        var a = Measurement(); a.source = "phantom"; a.notes = "MMH fixture"
        var b = Measurement(); b.source = "phantom"; b.notes = "review fixture"
        let aID = await mmh.repo.insertMeasurement(a)
        let bID = await review.repo.insertMeasurement(b)
        XCTAssertNotNil(aID); XCTAssertEqual(aID, bID)
        let aSaved = await mmh.repo.measurement(id: try XCTUnwrap(aID))
        let bSaved = await review.repo.measurement(id: try XCTUnwrap(bID))
        XCTAssertEqual(aSaved?.notes, "MMH fixture")
        XCTAssertEqual(bSaved?.notes, "review fixture")
    }
    func testSameMediaNameDoesNotCrossReadOrDelete() throws {
        let a = try MedicalLocalStores(scope: .mmh, support: root)
        let b = try MedicalLocalStores(scope: .syntheticReview, support: root)
        for (scope, store, bytes) in [(MedicalDataScope.mmh, a.images, Data([1,2])), (.syntheticReview, b.images, Data([3,4]))] {
            let name = try XCTUnwrap(store.saveRaw(bytes))
            try FileManager.default.moveItem(at: scope.images(under: root).appendingPathComponent(name),
                to: scope.images(under: root).appendingPathComponent("same.enc"))
        }
        XCTAssertEqual(a.images.rawBytes("same.enc"), Data([1,2]))
        XCTAssertEqual(b.images.rawBytes("same.enc"), Data([3,4]))
        a.images.delete("same.enc")
        XCTAssertNil(a.images.rawBytes("same.enc"))
        XCTAssertEqual(b.images.rawBytes("same.enc"), Data([3,4]))
    }
    func testSameImageDepthAndUploadStateStayWithTheirScope() throws {
        let a = try MedicalLocalStores(scope: .mmh, support: root)
        let b = try MedicalLocalStores(scope: .syntheticReview, support: root)
        XCTAssertTrue(DepthStore.attach(imagePath: "same.enc", capture: capture(0.3), store: a.images))
        XCTAssertTrue(DepthStore.attach(imagePath: "same.enc", capture: capture(0.5), store: b.images))
        XCTAssertNotEqual(DepthStore.load(imagePath: "same.enc", store: a.images)?.raw,
                          DepthStore.load(imagePath: "same.enc", store: b.images)?.raw)
        DepthStore.markUploaded(imagePath: "same.enc", store: a.images)
        XCTAssertEqual(DepthStore.lookup(imagePath: "same.enc", store: a.images)?["uploaded"], "1")
        XCTAssertNil(DepthStore.lookup(imagePath: "same.enc", store: b.images)?["uploaded"])
        let before = try XCTUnwrap(DepthStore.load(imagePath: "same.enc", store: b.images)?.raw)
        DepthStore.purge(imagePath: "same.enc", store: a.images)
        XCTAssertNil(DepthStore.lookup(imagePath: "same.enc", store: a.images))
        XCTAssertEqual(DepthStore.load(imagePath: "same.enc", store: b.images)?.raw, before)
    }
    func testOutboxesRemainIsolatedAfterReopening() throws {
        let a = try MedicalLocalStores(scope: .mmh, support: root)
        let b = try MedicalLocalStores(scope: .syntheticReview, support: root)
        var m = Measurement(); m.id = 42; m.imageId = "same-synthetic-image"; m.source = "phantom"
        for (stores, payload) in [(a, "MMH payload"), (b, "review payload")] {
            _ = try stores.outbox.enqueue(measurement: m, payload: Data(payload.utf8),
                server: "https://synthetic.invalid", configuredUser: "test", actor: "fixture:test",
                depth: nil, requiresDepth: false)
        }
        let aAgain = try MedicalLocalStores(scope: .mmh, support: root)
        let bAgain = try MedicalLocalStores(scope: .syntheticReview, support: root)
        XCTAssertEqual(aAgain.outbox.jobs.count, 1); XCTAssertEqual(bAgain.outbox.jobs.count, 1)
        XCTAssertEqual(aAgain.outbox.jobs.first?.annotationPayload, Data("MMH payload".utf8))
        XCTAssertEqual(bAgain.outbox.jobs.first?.annotationPayload, Data("review payload".utf8))
    }
    func testCorruptDepthIndexIsNeverReplacedByAttachOrPurge() throws {
        let stores = try MedicalLocalStores(scope: .mmh, support: root)
        let corrupt = Data("not an index".utf8)
        try corrupt.write(to: stores.images.depthIndexURL)
        XCTAssertFalse(DepthStore.attach(imagePath: "same.enc", capture: capture(0.3), store: stores.images))
        DepthStore.purge(imagePath: "same.enc", store: stores.images)
        DepthStore.markUploaded(imagePath: "same.enc", store: stores.images)
        XCTAssertEqual(try Data(contentsOf: stores.images.depthIndexURL), corrupt)
        XCTAssertEqual(stores.images.totalBytes(), 0)
    }
    func testFailedIndexWriteDoesNotLeaveNewSidecars() throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let blocked = root.appendingPathComponent("blocked")
        try Data([7]).write(to: blocked)
        let store = LocalImageStore(directory: root.appendingPathComponent("media"), depthIndexURL: blocked.appendingPathComponent("index.json"))
        XCTAssertFalse(DepthStore.attach(imagePath: "same.enc", capture: capture(0.3), store: store))
        XCTAssertEqual(store.totalBytes(), 0)
        XCTAssertEqual(try Data(contentsOf: blocked), Data([7]))
    }
    func testMediaReferencesCannotEscapeDirectory() throws {
        let a = try MedicalLocalStores(scope: .mmh, support: root)
        let name = try XCTUnwrap(a.images.saveRaw(Data([9])))
        let other = LocalImageStore(directory: root.appendingPathComponent("other"))
        let path = "../wound_images/" + name
        XCTAssertFalse(other.exists(path)); XCTAssertNil(other.rawBytes(path))
        other.delete(path)
        XCTAssertEqual(a.images.rawBytes(name), Data([9]))
    }
}
