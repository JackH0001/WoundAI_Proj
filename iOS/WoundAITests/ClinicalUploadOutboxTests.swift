import XCTest
import CryptoKit
#if WOUND_INSTITUTION_TESTS
@testable import WoundAIInstitution
#else
@testable import WoundMeasurementApp
#endif

@MainActor
final class ClinicalUploadOutboxTests: XCTestCase {
    private enum Failure: Error { case offline }
    private var directory: URL!
    override func setUp() {
        super.setUp()
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }
    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
        super.tearDown()
    }
    private func enqueue(_ box: ClinicalUploadOutbox) throws -> String {
        var m = Measurement(); m.id = 42; m.imageId = "synthetic-image"; m.source = "phantom"
        return try box.enqueue(measurement: m, payload: Data("exact annotation bytes".utf8),
            server: "https://original.invalid", configuredUser: "dr01", actor: "mmh:dr01",
            depth: (Data([1,2,3,4]), ["source_exif_orientation":6]), requiresDepth: true)
    }
    func testEncryptedSnapshotSurvivesReopenWithoutCredentials() throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let id = try enqueue(box)
        let disk = try Data(contentsOf: directory.appendingPathComponent(id + ".enc"))
        XCTAssertNil(disk.range(of: Data("exact annotation bytes".utf8)))
        let reopened = ClinicalUploadOutbox(directory: directory)
        let job = try XCTUnwrap(reopened.jobs.first)
        XCTAssertEqual(job.annotationPayload, Data("exact annotation bytes".utf8))
        XCTAssertEqual(job.depthRaw, Data([1,2,3,4]))
        XCTAssertEqual(job.actor, "mmh:dr01")
        XCTAssertFalse(job.completed)
    }
    func testSamePendingSnapshotDoesNotCreateAnotherJob() throws {
        let box = ClinicalUploadOutbox(directory: directory)
        XCTAssertEqual(try enqueue(box), try enqueue(box))
        XCTAssertEqual(box.pendingCount, 1)
    }
    func testReopenAfterDepthFailureResumesOnlyDepth() async throws {
        let first = ClinicalUploadOutbox(directory: directory)
        let id = try enqueue(first)
        try await first.process(id: id, allowed: { true }, annotate: { _ in .enqueued(note:nil, revisionID:"0123456789abcdef") },
            sendDepth: { _ in throw Failure.offline }, acknowledgeRecord: { _ in })
        let reopened = ClinicalUploadOutbox(directory: directory)
        XCTAssertTrue(try XCTUnwrap(reopened.jobs.first).annotationConfirmed)
        XCTAssertEqual(reopened.pendingCount, 1)
        try await reopened.process(id: id, allowed: { true },
            annotate: { _ in XCTFail("must not resend confirmed annotation"); throw Failure.offline },
            sendDepth: { job in
                XCTAssertEqual(job.annotationRevisionID, "0123456789abcdef")
                XCTAssertEqual(job.depthRaw, Data([1,2,3,4])) }, acknowledgeRecord: { _ in })
        let completed = try XCTUnwrap(ClinicalUploadOutbox(directory: directory).jobs.first)
        XCTAssertTrue(completed.completed)
        XCTAssertTrue(completed.depthConfirmed)
        XCTAssertNil(completed.depthRaw)
        XCTAssertTrue(completed.annotationPayload.isEmpty)
    }
    func testLostAnnotationResponseReplaysExactBytesAndDuplicateContinues() async throws {
        let first = ClinicalUploadOutbox(directory: directory)
        let id = try enqueue(first)
        try await first.process(id: id, allowed: { true }, annotate: { payload in
            XCTAssertEqual(payload, Data("exact annotation bytes".utf8)); throw Failure.offline
        }, sendDepth: { _ in XCTFail("unacknowledged annotation") }, acknowledgeRecord: { _ in XCTFail() })
        let reopened = ClinicalUploadOutbox(directory: directory)
        try await reopened.process(id: id, allowed: { true }, annotate: { payload in
            XCTAssertEqual(payload, Data("exact annotation bytes".utf8)); return .duplicateSkipped(note:"stored")
        }, sendDepth: { _ in }, acknowledgeRecord: { _ in })
        XCTAssertTrue(try XCTUnwrap(reopened.jobs.first).completed)
    }
    func testWithdrawalBetweenStagesStopsDepthAndKeepsPending() async throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let id = try enqueue(box)
        var consent = true
        try await box.process(id: id, allowed: { consent }, annotate: { _ in .enqueued(note:nil) },
            sendDepth: { _ in XCTFail("withdrawn") }, acknowledgeRecord: { _ in consent = false })
        let job = try XCTUnwrap(ClinicalUploadOutbox(directory: directory).jobs.first)
        XCTAssertTrue(job.annotationConfirmed)
        XCTAssertFalse(job.depthConfirmed)
        XCTAssertFalse(job.completed)
        XCTAssertNotNil(job.depthRaw)
    }
    func testChangedServerUserOrActorCannotRetargetSavedJob() throws {
        let box = ClinicalUploadOutbox(directory: directory); _ = try enqueue(box)
        let job = try XCTUnwrap(box.jobs.first)
        XCTAssertTrue(ClinicalUploadOutbox.bindingMatches(job, server:job.server, user:job.configuredUser, actor:job.actor))
        XCTAssertFalse(ClinicalUploadOutbox.bindingMatches(job, server:"https://other.invalid", user:job.configuredUser, actor:job.actor))
        XCTAssertFalse(ClinicalUploadOutbox.bindingMatches(job, server:job.server, user:"nurse", actor:job.actor))
        XCTAssertFalse(ClinicalUploadOutbox.bindingMatches(job, server:job.server, user:job.configuredUser, actor:"other:dr01"))
    }
    func testCorruptOutboxFailsClosed() throws {
        let box = ClinicalUploadOutbox(directory: directory); let id = try enqueue(box)
        try Data("corrupt".utf8).write(to:directory.appendingPathComponent(id + ".enc"))
        let reopened = ClinicalUploadOutbox(directory:directory)
        XCTAssertNotNil(reopened.storageIssue)
        XCTAssertThrowsError(try enqueue(reopened))
    }
    func testMissingRequiredRawDepthCannotEnterQueue() throws {
        let box = ClinicalUploadOutbox(directory:directory)
        var m = Measurement(); m.imageId = "fixture"
        XCTAssertThrowsError(try box.enqueue(measurement:m,payload:Data(),server:"https://a.invalid",configuredUser:"dr",actor:"a:dr",depth:nil,requiresDepth:true))
        XCTAssertTrue(box.jobs.isEmpty)
    }
    func testAnnotationReceiptRejectsUnknownMalformedOrMismatchedSuccess() throws {
        for body in ["{}", "not json", "{\"status\":\"enqueued\",\"code\":\"other\"}",
            "{\"status\":\"unknown\",\"code\":\"WD\"}",
            "{\"status\":\"enqueued\",\"code\":\"WD\",\"image_id\":\"other\"}"] {
            XCTAssertThrowsError(try BackendClient.annotationReceipt(Data(body.utf8),code:"WD",imageID:"image"))
        }
        XCTAssertEqual(try BackendClient.annotationReceipt(Data("{\"status\":\"enqueued\",\"code\":\"WD\",\"image_id\":\"image\"}".utf8),code:"WD",imageID:"image"), .enqueued(note:nil))
    }
    func testLegacyPendingJobRecoversRevisionBeforeSendingDepth() async throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let id = try enqueue(box)
        try await box.process(id:id, allowed:{true}, annotate:{ _ in .enqueued(note:nil) },
            sendDepth:{ _ in throw Failure.offline }, acknowledgeRecord:{ _ in })
        let reopened = ClinicalUploadOutbox(directory: directory)
        var requested = false
        try await reopened.process(id:id, allowed:{true}, annotate:{ _ in
            requested = true
            return .duplicateSkipped(note:"existing", revisionID:"fedcba9876543210")
        }, sendDepth:{ job in
            XCTAssertTrue(requested)
            XCTAssertEqual(job.annotationRevisionID, "fedcba9876543210")
        }, acknowledgeRecord:{ _ in })
        XCTAssertTrue(try XCTUnwrap(reopened.jobs.first).completed)
    }
    func testRevisionReceiptRejectsWrongImageOrMalformedID() throws {
        for (image, revision) in [("wrong", "0123456789abcdef"), ("image", "invalid")] {
            let data = try JSONSerialization.data(withJSONObject:["status":"duplicate_skipped",
                "code":"WD", "image_id":image, "annotation_receipt_id":revision])
            XCTAssertThrowsError(try BackendClient.annotationReceipt(data, code:"WD", imageID:"image"))
        }
    }
    func testDepthReceiptRequiresImageRevisionAndBothPayloadHashes() throws {
        let raw = Data([1,2,3,4]), metadata = Data("{}".utf8)
        let revision = "0123456789abcdef"
        let body = depthReceiptFixture(raw: raw, metadata: metadata, image: "image", revision: revision)
        let valid = try JSONSerialization.data(withJSONObject: body)
        let receipt = try BackendClient.depthReceipt(valid, imageID: "image", raw: raw,
            requestMetadata: metadata, annotationRevisionID: revision)
        XCTAssertEqual(receipt.annotationRevisionID, revision)
        for field in ["status", "image_id", "annotation_receipt_id", "depth_id", "depth_sha256",
                      "request_meta_sha256", "meta_sha256", "payload_sha256", "depth_revision_id",
                      "depth_receipt_id", "payload_hash_version", "replaced_previous"] {
            for altered: Any? in [nil, "wrong", true, 0] {
                var invalid = body
                invalid[field] = altered
                let encoded = try JSONSerialization.data(withJSONObject: invalid)
                XCTAssertThrowsError(try BackendClient.depthReceipt(encoded, imageID: "image", raw: raw,
                    requestMetadata: metadata, annotationRevisionID: revision), field)
            }
        }
        XCTAssertThrowsError(try BackendClient.depthReceipt(valid, imageID: "image", raw: raw,
            requestMetadata: Data("{ }".utf8), annotationRevisionID: revision))
        XCTAssertThrowsError(try BackendClient.depthReceipt(valid, imageID: "image", raw: raw,
            requestMetadata: metadata, annotationRevisionID: "fedcba9876543210"))
    }
}

func depthReceiptFixture(raw: Data, metadata: Data, image: String, revision: String) -> [String: Any] {
    func hash(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }
    let metaHash = SHA256.hash(data: metadata)
    let rawHash = SHA256.hash(data: raw)
    let payload = hash(Data("woundai-medical-depth-v1\0".utf8) + Data(rawHash) + Data(metaHash))
    return ["status": "stored", "image_id": image, "annotation_receipt_id": revision,
            "depth_id": String(hash(raw).prefix(16)), "depth_sha256": hash(raw),
            "request_meta_sha256": hash(metadata), "meta_sha256": hash(metadata),
            "payload_sha256": payload, "payload_hash_version": 1,
            "depth_revision_id": String(payload.prefix(16)),
            "depth_receipt_id": hash(Data((revision + "|" + payload).utf8)), "replaced_previous": false]
}
