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
    func testCapacityWaitSurvivesRestartAndOnlyRetriesUnconfirmedDepth() async throws {
        var clock = Date()
        let due = clock.addingTimeInterval(300)
        let box = ClinicalUploadOutbox(directory: directory, now: { clock })
        let id = try enqueue(box)
        try await box.process(id: id, allowed: { true }, annotate: { _ in .enqueued(note: nil, revisionID: "0123456789abcdef") },
            sendDepth: { _ in throw ResearchCapacityWait(notBefore: due) }, acknowledgeRecord: { _ in })
        let reopened = ClinicalUploadOutbox(directory: directory, now: { clock })
        XCTAssertEqual(reopened.jobs.first?.capacityNotBefore, due)
        XCTAssertEqual(reopened.jobs.first?.annotationRevisionID, "0123456789abcdef")
        XCTAssertEqual(reopened.jobs.first?.depthRaw, Data([1,2,3,4]))
        try await reopened.process(id: id, allowed: { true }, annotate: { _ in XCTFail(); throw Failure.offline },
            sendDepth: { _ in XCTFail("cannot bypass capacity wait") }, acknowledgeRecord: { _ in XCTFail() })
        clock = due
        try await reopened.process(id: id, allowed: { true }, annotate: { _ in XCTFail(); throw Failure.offline },
            sendDepth: { job in XCTAssertEqual(job.depthRaw, Data([1,2,3,4])); XCTAssertEqual(job.annotationRevisionID,"0123456789abcdef") }, acknowledgeRecord: { _ in })
        XCTAssertEqual(reopened.jobs.first?.completed, true)
    }
    func testRetryAfterContractUsesLargestHintAndServerClock() throws {
        let now = Date(timeIntervalSince1970: 1000)
        func delay(_ header: String?, _ body: String, _ date: String? = nil) -> Double {
            ResearchCapacityWait.deadline(retryAfter: header, serverDate: date, data: Data(body.utf8), now: now, jitter: 0).timeIntervalSince(now)
        }
        XCTAssertEqual(delay("120", "{\"retry_after\":180}"), 180)
        XCTAssertEqual(delay("Sat, 17 Oct 2026 03:05:00 GMT", "{}", "Sat, 17 Oct 2026 03:00:00 GMT"), 300)
        XCTAssertEqual(delay("-10", "{\"retry_after\":true}"), 60)
        XCTAssertEqual(delay("NaN", "{\"retry_after\":-1}"), 60)
        XCTAssertEqual(delay("90000", "{}"), 3600)
        XCTAssertEqual(delay("1e300", "{}"), 3600)
        XCTAssertEqual(ResearchCapacityWait.privacyDeadline(.distantFuture, now: now).timeIntervalSince(now), 300)
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
    private func countPayload(_ ratio: Double, image: String = "fixture") throws -> Data {
        try JSONSerialization.data(withJSONObject: ["image_id": image, "wound_mask_sha256": "same-mask",
            "tissue_edit_ratio": ratio, "tissue_edit_px": ratio > 1 ? 12 : 0,
            "tissue_edited": ratio > 0], options: [.sortedKeys])
    }
    private func countJob(_ box: ClinicalUploadOutbox, ratio: Double, actor: String = "mmh:dr01",
                          image: String = "fixture", depth: Data = Data([1,2])) throws -> String {
        var m = Measurement(); m.id = 5; m.imageId = image; m.source = "phantom"
        return try box.enqueue(measurement: m, payload: countPayload(ratio, image: image),
            server: "https://original.invalid", configuredUser: "dr01", actor: actor,
            depth: (depth, ["capture":"unchanged"]), requiresDepth: true)
    }
    private func rejectCount(_ box: ClinicalUploadOutbox, id: String) async throws {
        try await box.process(id: id, allowed: { true },
            annotate: { _ in .rejected(message: "tissue_edit_ratio range/type") },
            sendDepth: { _ in XCTFail("unaccepted revision") }, acknowledgeRecord: { _ in XCTFail() })
    }
    func testExplicitCountRepairRetainsRejectedBytesAndUnblocksNewRevisionAfterRestart() async throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let oldID = try countJob(box, ratio: 1.2)
        try await rejectCount(box, id: oldID)
        XCTAssertTrue(try XCTUnwrap(box.jobs.first).needsTissueCountRepair)
        XCTAssertFalse(box.blocksSubmission(recordID: 5))
        let newID = try countJob(box, ratio: 0)
        let restarted = ClinicalUploadOutbox(directory: directory)
        let rejected = try XCTUnwrap(restarted.jobs.first)
        XCTAssertEqual(rejected.supersededBy, newID)
        XCTAssertEqual(rejected.annotationPayload, try countPayload(1.2))
        XCTAssertEqual(rejected.depthRaw, Data([1,2]))
        XCTAssertFalse(rejected.completed)
        XCTAssertFalse(rejected.blocksNewSubmission)
        XCTAssertEqual(restarted.pendingCount, 1)
        XCTAssertTrue(restarted.blocksSubmission(recordID: 5))
        try await restarted.process(id: oldID, allowed: { true },
            annotate: { _ in XCTFail("superseded job replayed"); throw Failure.offline },
            sendDepth: { _ in XCTFail() }, acknowledgeRecord: { _ in XCTFail() })
        try await restarted.process(id: newID, allowed: { true },
            annotate: { body in
                XCTAssertEqual(body, try self.countPayload(0))
                return .enqueued(note: nil, revisionID: "0123456789abcdef")
            }, sendDepth: { job in
                XCTAssertEqual(job.annotationRevisionID, "0123456789abcdef")
                XCTAssertEqual(job.depthRaw, Data([1,2]))
            }, acknowledgeRecord: { _ in })
        XCTAssertEqual(restarted.pendingCount, 0)
        XCTAssertFalse(restarted.blocksSubmission(recordID: 5))
        XCTAssertTrue(try XCTUnwrap(restarted.latest(recordID: 5)).completed)
    }
    func testRepairCannotRetargetImageActorOrDepthOrHideAnUnconfirmedNetworkOutcome() async throws {
        for mode in 0..<4 {
            let folder = directory.appendingPathComponent(String(mode))
            let box = ClinicalUploadOutbox(directory: folder)
            let oldID = try countJob(box, ratio: 1.2)
            if mode != 3 { try await rejectCount(box, id: oldID) }
            _ = try countJob(box, ratio: 0, actor: mode == 0 ? "other:dr01" : "mmh:dr01",
                image: mode == 1 ? "different-image" : "fixture", depth: mode == 2 ? Data([9]) : Data([1,2]))
            XCTAssertNil(box.jobs.first?.supersededBy)
            XCTAssertEqual(box.pendingCount, 2)
        }
    }
    func testInvalidCountReplacementCannotRetireRejectedJob() async throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let id = try countJob(box, ratio: 1.2)
        try await rejectCount(box, id: id)
        _ = try countJob(box, ratio: 1.3)
        XCTAssertNil(box.jobs.first?.supersededBy)
    }
    func testSupersededDiagnosticSnapshotStillExpiresAfterNinetyDays() async throws {
        let box = ClinicalUploadOutbox(directory: directory)
        let oldID = try countJob(box, ratio: 1.2)
        try await rejectCount(box, id: oldID)
        _ = try countJob(box, ratio: 0)
        var old = try XCTUnwrap(box.jobs.first)
        old.measurement.timestamp = Date().addingTimeInterval(-91 * 86400)
        let encrypted = try XCTUnwrap(PhiCrypto.encryptBytes(JSONEncoder().encode(old)))
        try encrypted.write(to: directory.appendingPathComponent(oldID + ".enc"))
        let restarted = ClinicalUploadOutbox(directory: directory)
        XCTAssertNil(restarted.storageIssue)
        XCTAssertEqual(restarted.jobs.count, 1)
        XCTAssertEqual(restarted.pendingCount, 1)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent(oldID + ".enc").path))
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


extension ClinicalUploadOutboxTests {
    func testNativeWoundPayloadPreservesHoleIslandAndUnknownTissue() throws {
        var mask = [UInt8](repeating: 0, count: 64)
        for y in 1...6 { for x in 1...6 { mask[y * 8 + x] = 1 } }
        mask[27] = 0; mask[0] = 1
        var tissue = mask.map { $0 * 3 }; tissue[9] = 0
        let raster = EditRaster(mask: mask, tissue: tissue, origMask: mask, rx0: 0, ry0: 0,
                                mw: 8, mh: 8, mScale: 0.25, cm2PerPx: nil)
        let png = try XCTUnwrap(TissueMaskCodec.encodeBase64(tissue: tissue, mask: mask, mw: 8, mh: 8))
        let data = try BackendClient.annotationPayload(code: "WD", gtPolygon: [[0,0],[31,0],[31,31]],
            exudate: 0, imageId: "image", imageW: 32, imageH: 32,
            tissueMaskPngBase64: png, tissueRaster: raster, consentTrain: true, doctorVerified: true)
        // Synthetic codec evidence for independent server-side decoding (no patient data).
        try data.write(to: FileManager.default.temporaryDirectory.appendingPathComponent("native-mask-contract-ios.json"))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(obj["mask_contract"] as? String, "woundai.annotation-masks/1")
        let raw = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(obj["wound_mask_png"] as? String)))
        let pixels = try XCTUnwrap(EditRasterCodec.rgbaFromPNG(raw, expectW: 8, expectH: 8))
        for i in 0..<64 { XCTAssertEqual(pixels[i * 4], mask[i], "pixel \(i)") }
        XCTAssertEqual(obj["wound_mask_sha256"] as? String, SHA256.hash(data: raw).map { String(format: "%02x", $0) }.joined())
        XCTAssertEqual(pixels[9 * 4], 1) // Unknown tissue is still inside the wound.
    }
    func testNativeWoundEncoderRejectsTruncationInvalidValuesAndEmptyMask() {
        for mask in [[UInt8](repeating: 0, count: 64), [UInt8](repeating: 2, count: 64),
                     [UInt8](repeating: 1, count: 63)] {
            XCTAssertNil(WoundMaskCodec.encodeBase64(mask: mask, mw: 8, mh: 8))
        }
    }
    func testPairedMaskReceiptRequiresBothHashesAndRevisionForDuplicateToo() throws {
        let wound = String(repeating: "a", count: 64), tissue = String(repeating: "b", count: 64)
        for status in ["enqueued", "duplicate_skipped"] {
            let body = ["status":status, "code":"WD", "image_id":"image",
                        "annotation_receipt_id":"0123456789abcdef", "mask_contract":"woundai.annotation-masks/1",
                        "wound_mask_sha256":wound, "tissue_mask_sha256":tissue]
            XCTAssertNoThrow(try BackendClient.annotationReceipt(JSONSerialization.data(withJSONObject: body),
                code:"WD", imageID:"image", woundSHA:wound, tissueSHA:tissue))
            for field in ["annotation_receipt_id", "mask_contract", "wound_mask_sha256", "tissue_mask_sha256"] {
                for value: String? in [nil, "wrong"] {
                    var invalid = body; invalid[field] = value
                    XCTAssertThrowsError(try BackendClient.annotationReceipt(JSONSerialization.data(withJSONObject: invalid),
                        code:"WD", imageID:"image", woundSHA:wound, tissueSHA:tissue), field)
                }
            }
        }
    }
    func testNewSubmissionRequiresPairedRasterAndRejectsErrorSuccess() throws {
        XCTAssertThrowsError(try BackendClient.annotationPayload(code:"WD",gtPolygon:[[0,0],[8,0],[8,8]],
            exudate:0,imageId:"image",imageW:32,imageH:32,consentTrain:true,doctorVerified:true))
        for key in ["error", "issues"] {
            let data = try JSONSerialization.data(withJSONObject:["status":"enqueued","code":"WD",
                "image_id":"image",key:"unexpected"])
            XCTAssertThrowsError(try BackendClient.annotationReceipt(data,code:"WD",imageID:"image"))
        }
    }
}

// PNG class labels must survive embedded color profiles without rendering transforms.
extension ClinicalUploadOutboxTests {
    func testLabelPNGPreservesAllChannelsWithLinearGamma() throws {
        let data = try XCTUnwrap(Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAABGdBTUEAAYagMeiWXwAAABtJREFUeJwFwQcBAAAAwqD5+lcWYKhyTJyqYwcBnQAuflVlkwAAAABJRU5ErkJggg=="))
        let rgba = try XCTUnwrap(EditRasterCodec.rgbaFromPNG(data, expectW: 3, expectH: 2))
        for i in 0..<6 {
            XCTAssertEqual(Array(rgba[(i * 4)..<(i * 4 + 4)]), [UInt8(i), UInt8(5-i), UInt8(i), 255])
        }
        let roundTrip = try XCTUnwrap(EditRasterCodec.pngFromRGBA(rgba, width: 3, height: 2))
        XCTAssertEqual(EditRasterCodec.rgbaFromPNG(roundTrip, expectW: 3, expectH: 2), rgba)
        XCTAssertNil(EditRasterCodec.rgbaFromPNG(data, expectW: 2, expectH: 3))
    }
    func testLabelPNGRejectsTranslucentLabels() throws {
        let data = try XCTUnwrap(Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAABGdBTUEAAYagMeiWXwAAAB5JREFUeJwFwYcBAAAIAiDn331uABRjOFkHywuzons/+AWpDDDniwAAAABJRU5ErkJggg=="))
        XCTAssertNil(EditRasterCodec.rgbaFromPNG(data, expectW: 3, expectH: 2))
    }
}
