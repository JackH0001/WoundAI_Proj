import XCTest
import SwiftUI
@testable import WoundLite

final class LiteRevisionSyncTests: XCTestCase {
    @MainActor
    func testLegacyExcessiveRevisionWaitIsBoundedOnLoad() throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var r = store.records[0]
        let pending = LitePendingRevision(revision: 1, payloadJSON: try XCTUnwrap(r.revisionJSON()))
        r.cloudSync?.pending = pending
        r.cloudSync?.capacityNotBefore = .distantFuture
        r.cloudSync?.automaticRetryAt = .distantFuture
        XCTAssertTrue(store.update(r))
        let started = Date()
        let reopened = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        let sync = try XCTUnwrap(reopened.records[0].cloudSync)
        let deadline = try XCTUnwrap(sync.capacityNotBefore)
        XCTAssertLessThanOrEqual(deadline.timeIntervalSince(started), 3601)
        XCTAssertGreaterThanOrEqual(deadline.timeIntervalSince(started), 3599)
        XCTAssertEqual(sync.automaticRetryAt, deadline)
        XCTAssertEqual(sync.pending, pending)
        let disk = try JSONDecoder().decode([LiteRecord].self,
            from: Data(contentsOf: dir.appendingPathComponent("records.json")))
        XCTAssertEqual(disk[0].cloudSync?.capacityNotBefore, deadline)
    }

    @MainActor
    private func legacyWaitFixture() throws -> (URL, LiteRecord, LitePendingRevision) {
        let (store, dir) = try setup()
        var row = store.records[0]
        let pending = LitePendingRevision(revision: 4, payloadJSON: try XCTUnwrap(row.revisionJSON()))
        row.cloudSync?.acknowledgedRevision = 3
        row.cloudSync?.acknowledgedDigest = "previous-revision-digest"
        row.cloudSync?.pending = pending
        row.cloudSync?.capacityNotBefore = .distantFuture
        row.cloudSync?.automaticRetryAt = .distantFuture
        row.cloudSync?.lastError = "original capacity message"
        XCTAssertTrue(store.update(row))
        return (dir, row, pending)
    }

    @MainActor
    func testRepairedWaitPersistsOnceAndExactRevisionSendsOnceAfterDeadline() async throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let start = Date()
        var now = start
        let first = LiteStore(fileURL: file, now: { now })
        let firstBytes = try Data(contentsOf: file)
        let due = start.addingTimeInterval(3600)
        let audit = LiteCapacityWaitRepair(originalNotBefore: .distantFuture,
            originalAutomaticRetryAt: .distantFuture, adjustedAt: start, boundedNotBefore: due)
        XCTAssertEqual(first.records[0].cloudSync?.capacityWaitRepairs, [audit])
        now = start.addingTimeInterval(1800)
        let second = LiteStore(fileURL: file, now: { now })
        XCTAssertEqual(try Data(contentsOf: file), firstBytes, "Restart cannot rewrite or slide the wait")
        XCTAssertTrue(second.duePendingRevisionIDs.isEmpty)
        var sends = 0
        await second.syncRevision(recordID: original.id, consent: { true }) { _, _ in sends += 1 }
        XCTAssertEqual(sends, 0)
        let waiting = LiteRevisionPresentation.make(record: second.records[0], syncing: false,
            consent: true, withdrawalBlocked: false, now: now)
        XCTAssertEqual(waiting.phase, .waiting); XCTAssertNil(waiting.retryTitle)
        now = due
        XCTAssertEqual(second.duePendingRevisionIDs, [original.id])
        let ready = LiteRevisionPresentation.make(record: second.records[0], syncing: false,
            consent: true, withdrawalBlocked: false, now: now)
        XCTAssertNotEqual(ready.phase, .waiting); XCTAssertNotNil(ready.retryTitle)
        for _ in 0..<3 {
            await second.syncRevision(recordID: original.id, consent: { true }) { binding, value in
                sends += 1
                XCTAssertEqual(binding, original.cloudSync?.binding)
                XCTAssertEqual(value, pending)
            }
        }
        XCTAssertEqual(sends, 1)
        XCTAssertTrue(second.duePendingRevisionIDs.isEmpty)
        let saved = LiteStore(fileURL: file, now: { now }).records[0]
        XCTAssertEqual(saved.cloudSync?.acknowledgedRevision, 4)
        XCTAssertEqual(saved.cloudSync?.acknowledgedDigest, pending.digest)
        XCTAssertNil(saved.cloudSync?.pending)
        XCTAssertEqual(saved.cloudSync?.capacityWaitRepairs, [audit])
        XCTAssertEqual(saved.imageName, original.imageName)
        XCTAssertEqual(saved.depthName, original.depthName)
    }

    @MainActor
    func testShortExpiredAbsentAndBoundaryRevisionWaitsRemainByteIdentical() throws {
        let (dir, original, _) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let now = Date()
        let waits: [Date?] = [now.addingTimeInterval(120), now.addingTimeInterval(-120), nil,
                              now.addingTimeInterval(3600)]
        let rows = waits.enumerated().map { i, wait -> LiteRecord in
            var row = original; row.id = "wait-\(i)"
            row.cloudSync?.capacityNotBefore = wait; row.cloudSync?.automaticRetryAt = wait
            return row
        }
        let raw = try JSONEncoder().encode(rows); try raw.write(to: file)
        var writes = 0
        let store = LiteStore(fileURL: file, now: { now }, writeIndex: { _, _ in writes += 1 })
        XCTAssertNil(store.storageError); XCTAssertEqual(writes, 0)
        XCTAssertEqual(try Data(contentsOf: file), raw)
        XCTAssertTrue(store.records.allSatisfy { $0.cloudSync?.capacityWaitRepairs == nil })
        XCTAssertEqual(store.records.map { $0.cloudSync?.capacityNotBefore }, waits)
    }

    @MainActor
    func testWaitRepairWriteFailurePreservesOriginalAndStopsSyncUntilDurableReload() async throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let raw = try Data(contentsOf: file)
        var now = Date(); var failWrites = true
        let store = LiteStore(fileURL: file, now: { now }, writeIndex: { data, url in
            if failWrites { throw CocoaError(.fileWriteOutOfSpace) }
            try HealthDataFiles.write(data, to: url)
        })
        XCTAssertTrue(store.needsReload)
        XCTAssertTrue(store.duePendingRevisionIDs.isEmpty)
        XCTAssertTrue(store.storageError?.contains("等待期限修復尚未保存") == true)
        XCTAssertEqual(try Data(contentsOf: file), raw)
        XCTAssertEqual(store.records[0].cloudSync?.pending, pending)
        XCTAssertNil(store.records[0].cloudSync?.capacityWaitRepairs)
        await store.syncRevision(recordID: original.id, consent: { true }) { _, _ in XCTFail("Not durable") }
        XCTAssertFalse(store.update(original))
        failWrites = false
        XCTAssertTrue(store.reload()); XCTAssertFalse(store.needsReload)
        now = now.addingTimeInterval(3601)
        var sends = 0
        await store.syncRevision(recordID: original.id, consent: { true }) { _, value in
            sends += 1; XCTAssertEqual(value, pending)
        }
        XCTAssertEqual(sends, 1)
    }

    @MainActor
    func testRepairedDueWaitDoesNotBypassConsentIdentityOrRetiredEpoch() async throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        var now = Date()
        let store = LiteStore(fileURL: file, now: { now })
        now = now.addingTimeInterval(3601)
        let raw = try Data(contentsOf: file)
        await store.syncRevision(recordID: original.id, consent: { false }) { _, _ in XCTFail("Consent off") }
        await store.syncRevision(recordID: original.id, consent: { true }, identity: { "other-installation" }) { _, _ in
            XCTFail("Different identity")
        }
        await store.syncRevision(recordID: original.id, consent: { true }, bindingAllowed: { _ in false }) { _, _ in
            XCTFail("Retired epoch")
        }
        XCTAssertEqual(try Data(contentsOf: file), raw)
        XCTAssertEqual(store.records[0].cloudSync?.pending, pending)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 3)
    }

    @MainActor
    func testLegacyPendingWaitWithoutAutomaticDateRestoresBoundedSchedule() throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        var row = original; row.cloudSync?.automaticRetryAt = nil
        try JSONEncoder().encode([row]).write(to: file)
        let now = Date(); let store = LiteStore(fileURL: file, now: { now })
        XCTAssertEqual(store.records[0].cloudSync?.automaticRetryAt, now.addingTimeInterval(3600))
        XCTAssertEqual(store.records[0].cloudSync?.pending, pending)
        XCTAssertNil(store.records[0].cloudSync?.capacityWaitRepairs?.first?.originalAutomaticRetryAt)
    }

    @MainActor
    func testRepairDoesNotInventPendingWorkForAcknowledgedOrUnboundRecord() throws {
        let (dir, original, _) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        var row = original
        row.cloudSync?.pending = nil; row.cloudSync?.automaticRetryAt = nil
        let acknowledgedJSON = try XCTUnwrap(row.revisionJSON())
        row.cloudSync?.acknowledgedDigest = LitePendingRevision.digest(acknowledgedJSON)
        var local = row; local.id = "unbound"; local.cloudSync = nil
        try JSONEncoder().encode([row, local]).write(to: file)
        let store = LiteStore(fileURL: file)
        XCTAssertNil(store.records[0].cloudSync?.pending)
        XCTAssertNil(store.records[0].cloudSync?.automaticRetryAt)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 3)
        XCTAssertNil(store.records[1].cloudSync)
    }

    @MainActor
    func testMultipleWaitRepairsUseOneAtomicWriteAndPreserveAttachmentBytes() throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let media = LocalImageStore()
        let image = try XCTUnwrap(media.saveRaw(Data([1, 9, 8, 4])))
        let depth = try XCTUnwrap(media.saveRaw(Data([3, 7, 6, 2])))
        defer { media.delete(image); media.delete(depth) }
        let rows = (0..<3).map { i -> LiteRecord in
            var row = original; row.id = "repair-\(i)"; row.imageName = image; row.depthName = depth
            return row
        }
        try JSONEncoder().encode(rows).write(to: file)
        var writes = 0
        let store = LiteStore(fileURL: file, writeIndex: { data, url in
            writes += 1; try HealthDataFiles.write(data, to: url)
        })
        XCTAssertEqual(writes, 1); XCTAssertEqual(store.records.count, 3)
        XCTAssertTrue(store.records.allSatisfy { $0.cloudSync?.pending == pending })
        XCTAssertEqual(Set(store.records.compactMap { $0.cloudSync?.capacityNotBefore }).count, 1)
        XCTAssertEqual(media.rawBytes(image), Data([1, 9, 8, 4]))
        XCTAssertEqual(media.rawBytes(depth), Data([3, 7, 6, 2]))
        XCTAssertEqual(store.records.map(\.imageName), rows.map(\.imageName))
        XCTAssertEqual(store.records.map(\.depthName), rows.map(\.depthName))
    }

    @MainActor
    func testRepairedWaitLostReceiptReusesExactRevisionWithoutMedia() async throws {
        let (dir, original, pending) = try legacyWaitFixture()
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        var now = Date(); var store = LiteStore(fileURL: file, now: { now })
        now = now.addingTimeInterval(3601)
        var requests: [LitePendingRevision] = []
        var received: [Int: String] = [:]
        let sender: (LiteCloudBinding, LitePendingRevision) async throws -> Void = { binding, value in
            XCTAssertEqual(binding, original.cloudSync?.binding)
            requests.append(value)
            if let old = received[value.revision] { XCTAssertEqual(old, value.payloadJSON) }
            received[value.revision] = value.payloadJSON
            let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(value.payloadJSON.utf8)) as? [String: Any])
            for field in ["image", "depth_map", "rgbd"] { XCTAssertNil(fields[field]) }
            if requests.count == 1 { throw URLError(.timedOut) }
        }
        await store.syncRevision(recordID: original.id, consent: { true }, send: sender)
        store = LiteStore(fileURL: file, now: { now })
        await store.syncRevision(recordID: original.id, consent: { true }, send: sender)
        await store.syncRevision(recordID: original.id, consent: { true }, send: sender)
        XCTAssertEqual(requests, [pending, pending])
        XCTAssertEqual(received.count, 1)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 4)
        XCTAssertNil(store.records[0].cloudSync?.pending)
        XCTAssertEqual(store.records[0].cloudSync?.capacityWaitRepairs?.count, 1)
    }

    @MainActor
    func testRetiredEpochPreservesPendingRevisionAndNeverEnqueuesOrSends() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        for hasPending in [false, true] {
            var r = store.records[0]
            if hasPending {
                let json = try XCTUnwrap(r.revisionJSON())
                r.cloudSync?.pending = LitePendingRevision(revision: 6, payloadJSON: json)
                r.cloudSync?.automaticRetryAt = .distantPast
            }
            XCTAssertTrue(store.update(r))
            let before = try Data(contentsOf: dir.appendingPathComponent("records.json"))
            await store.syncRevision(recordID: r.id, consent: { true }, bindingAllowed: { _ in false }) { _, _ in
                XCTFail("Retired bindings must never upload even with renewed global consent")
            }
            XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent("records.json")), before)
        }
    }
    @MainActor
    func testCaptureClearsOnlyAfterSavedRecordAndDoesNotDeletePendingWork() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let vm = LiteMeasureVM()
        XCTAssertEqual(vm.captureButtonTitle, "拍攝傷口")
        let picture = UIGraphicsImageRenderer(size: CGSize(width: 16, height: 16)).image { c in
            UIColor.red.setFill(); c.fill(CGRect(x: 0, y: 0, width: 16, height: 16))
        }
        _ = await vm.ingest(picture, depth: nil) // no cloud call without depth
        XCTAssertEqual(vm.captureButtonTitle, "重新拍攝")
        XCTAssertNotNil(vm.captureJPEG)
        vm.savedId = "not-saved"
        XCTAssertNil(vm.finishSavedCapture(in: store))
        XCTAssertNotNil(vm.image)
        var r = store.records[0]
        let payload = try XCTUnwrap(r.revisionJSON())
        r.cloudSync?.pending = LitePendingRevision(revision: 1, payloadJSON: payload)
        XCTAssertTrue(store.update(r))
        let before = try Data(contentsOf: dir.appendingPathComponent("records.json"))
        vm.savedId = r.id
        XCTAssertEqual(vm.finishSavedCapture(in: store), r.id)
        XCTAssertNil(vm.image); XCTAssertNil(vm.captureJPEG); XCTAssertNil(vm.depth)
        XCTAssertNil(vm.estimate); XCTAssertTrue(vm.polys.isEmpty)
        XCTAssertNil(vm.note); XCTAssertNil(vm.savedId); XCTAssertNil(vm.cloudBinding)
        XCTAssertEqual(vm.captureButtonTitle, "拍攝傷口")
        XCTAssertEqual(try Data(contentsOf: dir.appendingPathComponent("records.json")), before)
        XCTAssertEqual(LiteStore(fileURL: dir.appendingPathComponent("records.json")).records[0].cloudSync?.pending, r.cloudSync?.pending)
        XCTAssertNil(vm.finishSavedCapture(in: store), "Repeated completion cannot navigate/save twice")
    }

    @MainActor
    func testSavePersistsFirstRevisionBeforeNavigationAndRestart() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var r = store.records[0]; r.id = "new-capture"
        let saved = try XCTUnwrap(store.saveMeasurement(r, selectedWoundID: "",
            enteredLocation: LiteWoundLocation(side: "right", site: "knee_back"), queueResearchSync: true))
        let pending = try XCTUnwrap(saved.cloudSync?.pending)
        XCTAssertEqual(pending.revision, 1)
        XCTAssertEqual(pending.payloadJSON, try saved.revisionJSON())
        XCTAssertNotNil(saved.cloudSync?.automaticRetryAt)
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        XCTAssertEqual(restarted.records.first?.cloudSync?.pending, pending)
        var attempts = 0
        await restarted.syncRevision(recordID: saved.id, consent: { true }) { _, value in
            attempts += 1; XCTAssertEqual(value, pending)
        }
        await restarted.syncRevision(recordID: saved.id, consent: { true }) { _, _ in attempts += 1 }
        XCTAssertEqual(attempts, 1)
        XCTAssertEqual(restarted.records.first?.cloudSync?.acknowledgedRevision, 1)
        XCTAssertEqual(restarted.records.first?.imageName, r.imageName)
        XCTAssertEqual(restarted.records.first?.depthName, r.depthName)
    }

    @MainActor
    func testLocalSaveDoesNotQueueWithoutConsentOrCloudBinding() throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        for bound in [true, false] {
            var r = store.records.last!; r.id = UUID().uuidString
            if !bound { r.cloudSync = nil }
            let saved = try XCTUnwrap(store.saveMeasurement(r, selectedWoundID: "",
                enteredLocation: LiteWoundLocation(side: "right", site: "knee_back"),
                queueResearchSync: !bound))
            XCTAssertNil(saved.cloudSync?.pending)
            XCTAssertNil(saved.cloudSync?.automaticRetryAt)
        }
    }

    @MainActor
    func testFailedSaveKeepsDraftAndOldIndexUnchanged() throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("records.json")
        let original = try Data(contentsOf: file)
        var r = store.records[0]; r.id = "not-saved"
        let vm = LiteMeasureVM(); vm.image = UIImage(); vm.savedId = r.id
        XCTAssertNil(store.saveMeasurement(r, selectedWoundID: "missing-group",
            enteredLocation: LiteWoundLocation(side: "right", site: "knee_back"), queueResearchSync: true))
        XCTAssertNil(vm.finishSavedCapture(in: store)); XCTAssertNotNil(vm.image)
        XCTAssertEqual(try Data(contentsOf: file), original)
        XCTAssertEqual(store.records.count, 1)
    }

    @MainActor
    func testAuthenticationFailureKeepsPendingWithoutBlindAutomaticRetries() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        await store.syncRevision(recordID: "record", consent: { true }) { _, _ in
            throw BackendError.http(status: 401, message: "lite_attestation_required")
        }
        let restored = LiteStore(fileURL: dir.appendingPathComponent("records.json")).records[0]
        XCTAssertEqual(restored.cloudSync?.pending?.revision, 1)
        XCTAssertEqual(restored.cloudSync?.acknowledgedRevision, 0)
        XCTAssertNil(restored.cloudSync?.automaticRetryAt)
        XCTAssertTrue(restored.cloudSync?.lastError?.contains("驗證未通過（401）") == true)
        XCTAssertEqual(restored.imageName, "unused")
    }

    @MainActor
    func testNoLidarStillRendersRecordAndSettingsTabsWithoutConsentWall() async throws {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 430, height: 932))
        let host = UIHostingController(rootView: LiteRootView(lidarAvailable: false))
        window.rootViewController = host; window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        host.view.setNeedsLayout(); host.view.layoutIfNeeded()
        try await Task.sleep(nanoseconds: 300_000_000)
        func tabBar(_ view: UIView) -> UITabBar? {
            if let bar = view as? UITabBar { return bar }
            return view.subviews.lazy.compactMap { tabBar($0) }.first
        }
        let bar = try XCTUnwrap(tabBar(host.view))
        XCTAssertEqual(bar.items?.compactMap(\.title), ["量測", "紀錄", "設定"])
        XCTAssertFalse(bar.isHidden); XCTAssertNil(host.presentedViewController)
        let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        let attachment = XCTAttachment(image: image)
        attachment.name = "lite-no-lidar-navigation"; attachment.lifetime = .keepAlways; add(attachment)
    }

    @MainActor
    private func setup() throws -> (LiteStore, URL) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        var r = LiteRecord(id: "record", dateISO: "2026-10-03", surfaceCm2: 4, projectedCm2: 3.9,
                           quality: "ok", imageName: "unused", source: "cloud",
                           polysJson: "[[[0,0],[8,0],[8,8]]]", depthName: nil)
        r.cloudSync = LiteCloudSyncState(binding: LiteCloudBinding(server: "https://original.invalid",
            anonID: "installation", imageID: "image", imageW: 16, imageH: 16))
        r.manuallyConfirmed = true
        XCTAssertTrue(store.add(r))
        return (store, dir)
    }
    private enum Offline: Error { case timeout }

    @MainActor
    func testCapacityWaitKeepsExactRevisionAcrossRestartAndManualRetry() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let due = Date().addingTimeInterval(300)
        var original: LitePendingRevision?
        await store.syncRevision(recordID: "record", consent: { true }) { _, pending in
            original = pending; throw ResearchCapacityWait(notBefore: due)
        }
        let reopened = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        XCTAssertEqual(reopened.records.first?.cloudSync?.pending, original)
        XCTAssertEqual(reopened.records.first?.cloudSync?.capacityNotBefore, due)
        await reopened.syncRevision(recordID: "record", consent: { true }) { _, _ in XCTFail("not due") }
        XCTAssertEqual(reopened.records.first?.cloudSync?.acknowledgedRevision, 0)
    }

    @MainActor
    func testUnchangedRecordDoesNotResendAcrossRestart() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var calls = 0
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { binding, p in
            calls += 1
            XCTAssertEqual(binding.server, "https://original.invalid")
            XCTAssertEqual(p.revision, 1)
        }
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        await restarted.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in calls += 1 }
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(restarted.records[0].cloudSync?.acknowledgedRevision, 1)
    }

    @MainActor
    func testTimeoutReusesExactPendingBytesAndRevisionAfterRestart() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var sent: LitePendingRevision?
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            sent = p; throw Offline.timeout
        }
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        XCTAssertNotNil(restarted.records[0].cloudSync?.pending)
        await restarted.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            XCTAssertEqual(p, sent)
        }
        XCTAssertNil(restarted.records[0].cloudSync?.pending)
    }

    @MainActor
    func testEditingDuringFlightPreservesNewValuesAndSendsNextRevision() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var sent: [LitePendingRevision] = []
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            sent.append(p)
            if p.revision == 1 {
                var r = store.records[0]; r.surfaceCm2 = 7; r.woundName = "new label"
                XCTAssertTrue(store.update(r))
            }
        }
        XCTAssertEqual(sent.map(\.revision), [1, 2])
        XCTAssertNotEqual(sent.first?.digest, sent.last?.digest)
        XCTAssertEqual(store.records[0].surfaceCm2, 7)
        XCTAssertEqual(store.records[0].woundName, "new label")
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 2)
    }

    @MainActor
    func testSuccessiveEditsWhileSendingDrainToLatestRevision() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var revisions: [Int] = []
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            revisions.append(p.revision)
            if p.revision < 4 {
                var r = store.records[0]; r.surfaceCm2 += 1; XCTAssertTrue(store.update(r))
            }
        }
        XCTAssertEqual(revisions, [1, 2, 3, 4])
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 4)
        XCTAssertTrue(store.records[0].cloudSyncDescription.contains("已確認同步"))
    }

    @MainActor
    func testRepeatTapWhileInFlightDoesNotSendAgain() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var calls = 0
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in
            calls += 1
            await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in calls += 1 }
        }
        XCTAssertEqual(calls, 1)
    }

    @MainActor
    func testConsentOffIdentityChangeOrMissingBindingNeverSend() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var calls = 0
        await store.syncRevision(recordID: "record", consent: { false }, identity: { "installation" }) { _, _ in calls += 1 }
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "other" }) { _, _ in calls += 1 }
        var old = store.records[0]; old.cloudSync = nil; XCTAssertTrue(store.update(old))
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in calls += 1 }
        XCTAssertEqual(calls, 0)
        XCTAssertTrue(store.records[0].cloudSyncDescription.contains("未列為已上傳"))
    }

    @MainActor
    func testUneditedAIOutputSyncsMeasurementWithoutBecomingManualLabel() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var r = store.records[0]; r.manuallyConfirmed = false; XCTAssertTrue(store.update(r))
        var calls = 0
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            calls += 1
            let payload = try JSONDecoder().decode(LiteRevisionPayload.self, from: Data(p.payloadJSON.utf8))
            XCTAssertEqual(payload.source, "ai"); XCTAssertEqual(payload.surface_cm2, 4)
            XCTAssertEqual(payload.consent_version, LitePrefs.consentVersion)
            let attachment = XCTAttachment(data: Data(p.payloadJSON.utf8), uniformTypeIdentifier: "public.json")
            attachment.name = "lite-current-ai-revision.json"; attachment.lifetime = .keepAlways
            self.add(attachment)
        }
        XCTAssertEqual(calls, 1)
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in calls += 1 }
        XCTAssertEqual(calls, 1)
        r = store.records[0]; r.manuallyConfirmed = true; r.source = "manual"; XCTAssertTrue(store.update(r))
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, p in
            calls += 1; XCTAssertEqual(p.revision, 2)
            let payload = try JSONDecoder().decode(LiteRevisionPayload.self, from: Data(p.payloadJSON.utf8))
            XCTAssertEqual(payload.source, "manual")
        }
        XCTAssertEqual(calls, 2)
    }

    @MainActor
    func testUnknownLegacySourceDoesNotInventAIProvenance() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        for source in ["manual", "local", "unknown"] {
            var r = store.records[0]; r.manuallyConfirmed = false; r.source = source; XCTAssertTrue(store.update(r))
            await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in XCTFail() }
        }
        var r = store.records[0]; r.manuallyConfirmed = nil; r.source = "cloud"; XCTAssertTrue(store.update(r))
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in XCTFail() }
    }

    @MainActor
    func testEditorWithUnchangedMaskPreservesAIContourAndProvenance() async throws {
        let vm = LiteMeasureVM()
        vm.source = "cloud"; vm.polys = [[[0,0],[8,0],[8,8]]]
        let original = vm.polys
        await vm.applyManual([[[1,1],[7,1],[7,7]]], correctionIoU: 1)
        XCTAssertEqual(vm.polys, original); XCTAssertEqual(vm.source, "cloud"); XCTAssertFalse(vm.hasManualContour)
        await vm.applyManual([[[1,1],[7,1],[7,7]]], correctionIoU: 0.9)
        XCTAssertEqual(vm.source, "manual"); XCTAssertTrue(vm.hasManualContour)
    }

    @MainActor
    func testPersistenceFailurePreventsNetworkRequest() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        try Data("changed on disk".utf8).write(to: dir.appendingPathComponent("records.json"))
        var calls = 0
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in calls += 1 }
        XCTAssertEqual(calls, 0)
        XCTAssertTrue(store.needsReload)
    }

    @MainActor
    func testDeletedRecordIsNotResurrectedByLateReceipt() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        await store.syncRevision(recordID: "record", consent: { true }, identity: { "installation" }) { _, _ in
            XCTAssertTrue(store.delete(store.records[0]))
        }
        XCTAssertTrue(store.records.isEmpty)
    }

    @MainActor
    func testRevokingConsentStopsNextRevisionAfterInflightRequest() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var consent = true, calls = 0
        await store.syncRevision(recordID: "record", consent: { consent }, identity: { "installation" }) { _, _ in
            calls += 1; consent = false
            var r = store.records[0]; r.surfaceCm2 = 5; XCTAssertTrue(store.update(r))
        }
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 1)
        XCTAssertTrue(store.records[0].cloudSyncDescription.contains("尚未同步"))
    }

    @MainActor
    func testOldJSONWithoutCloudStateStillLoads() throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        var old = store.records[0]; old.cloudSync = nil; old.manuallyConfirmed = nil
        XCTAssertTrue(store.update(old))
        let reopened = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        XCTAssertEqual(reopened.records.count, 1)
        XCTAssertNil(reopened.records[0].cloudSync)
    }
    @MainActor
    func testVerifiedOwnerSyncDoesNotDependOnLegacyPreferenceAndDoesNotResend() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let http = AttestHTTPDouble()
        let signer = try LiteAttestClient(origin: URL(string: "https://original.invalid")!, audience: "lite-test",
            device: AttestDeviceDouble(), persistence: AttestMemory(), transport: http,
            now: { Date(timeIntervalSince1970: 1000) })
        let backend = BackendClient(baseUrl: "https://original.invalid",
            liteTransport: LiteSignedCloudTransport(client: signer, consent: { true }))
        let owner = try await backend.liteInstallation()
        var r = store.records[0]; r.cloudSync?.binding.anonID = owner; XCTAssertTrue(store.update(r))
        let digest = LitePendingRevision.digest(try XCTUnwrap(r.revisionJSON()))
        await http.businessReply(["status": "stored", "image_id": "image", "revision": 1, "payload_sha256": digest])
        for _ in 0..<2 {
            await store.syncRevision(recordID: "record", consent: { true }, send: { binding, pending in
                try await backend.liteRevision(bindingAnonID: binding.anonID, imageID: binding.imageID,
                    revision: pending.revision, payloadJSON: pending.payloadJSON, digest: pending.digest)
            })
        }
        let calls = await http.snapshot(); XCTAssertEqual(calls.1.count, 1)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedDigest, digest)
        XCTAssertNil(store.records[0].cloudSync?.pending)
    }

    @MainActor
    func testUnprovableLegacyBindingAndPendingPayloadSurviveRestartUnchanged() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let original = try XCTUnwrap(store.records[0].cloudSync?.binding)
        let http = AttestHTTPDouble()
        let signer = try LiteAttestClient(origin: URL(string: original.server)!, audience: "lite-test",
            device: AttestDeviceDouble(), persistence: AttestMemory(), transport: http,
            now: { Date(timeIntervalSince1970: 1000) })
        let backend = BackendClient(baseUrl: original.server,
            liteTransport: LiteSignedCloudTransport(client: signer, consent: { true }))
        _ = try await backend.liteInstallation()
        await store.syncRevision(recordID: "record", consent: { true }, send: { binding, pending in
            try await backend.liteRevision(bindingAnonID: binding.anonID, imageID: binding.imageID,
                revision: pending.revision, payloadJSON: pending.payloadJSON, digest: pending.digest)
        })
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        let saved = try XCTUnwrap(restarted.records[0].cloudSync)
        XCTAssertEqual(saved.binding, original); XCTAssertEqual(saved.acknowledgedRevision, 0)
        XCTAssertNotNil(saved.pending)
        XCTAssertEqual(saved.pending?.payloadJSON, try restarted.records[0].revisionJSON())
        XCTAssertTrue(saved.lastError?.contains("原上傳身分") == true)
        let calls = await http.snapshot(); XCTAssertTrue(calls.1.isEmpty)
    }

    @MainActor
    func testProductionSenderKeepsOldServiceRevisionPendingWithoutRetargeting() async throws {
        let (store, dir) = try setup(); defer { try? FileManager.default.removeItem(at: dir) }
        let original = try XCTUnwrap(store.records[0].cloudSync?.binding)
        // Use the real default sender. It must reject before constructing a network request.
        await store.syncRevision(recordID: "record", consent: { true })
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        let saved = try XCTUnwrap(restarted.records[0].cloudSync)
        XCTAssertEqual(saved.binding, original)
        XCTAssertEqual(saved.acknowledgedRevision, 0)
        XCTAssertNotNil(saved.pending)
        XCTAssertTrue(saved.lastError?.contains("屬於舊服務") == true)
    }

}

final class LiteRevisionPresentationTests: XCTestCase {
    private func record() -> LiteRecord {
        var r = LiteRecord(id: "synthetic", dateISO: "2026-10-21", surfaceCm2: 80.36,
            projectedCm2: 80.07, quality: "ok", imageName: "preserved.jpg", source: "manual",
            polysJson: "[[[20,20],[80,20],[80,80],[20,80]]]", depthName: "preserved.depth")
        r.manuallyConfirmed = true
        r.cloudSync = LiteCloudSyncState(binding: LiteCloudBinding(server: "https://test.invalid",
            anonID: "owner", imageID: "original", imageW: 128, imageH: 128))
        return r
    }
    private func state(_ record: LiteRecord, syncing: Bool = false, consent: Bool = true,
                       withdrawn: Bool = false, now: Date = Date()) -> LiteRevisionPresentation {
        .make(record: record, syncing: syncing, consent: consent, withdrawalBlocked: withdrawn, now: now)
    }
    private func acknowledged(_ revision: Int = 1) throws -> LiteRecord {
        var r = record()
        r.cloudSync?.acknowledgedRevision = revision
        let json = try XCTUnwrap(r.revisionJSON())
        r.cloudSync?.acknowledgedDigest = LitePendingRevision.digest(json)
        return r
    }

    func testCurrentReceiptHidesUploadAndShowsExactRevision() throws {
        let r = try acknowledged(3)
        XCTAssertEqual(state(r).phase, .synced)
        XCTAssertTrue(state(r).title.contains("第 3 版"))
        XCTAssertNil(state(r).retryTitle)
    }

    func testChangedContourWithSameAreaIsStillPending() throws {
        var r = try acknowledged(3)
        r.polysJson = "[[[21,20],[81,20],[81,80],[21,80]]]"
        XCTAssertEqual(r.surfaceCm2, 80.36)
        XCTAssertEqual(state(r).phase, .pending)
        XCTAssertNotNil(state(r).retryTitle)
        XCTAssertEqual(state(r, syncing: true).phase, .syncing)
        XCTAssertTrue(state(r, syncing: true).title.contains("第 4 版"))
        XCTAssertNil(state(r, syncing: true).retryTitle)
    }

    func testOlderReceiptDoesNotClaimNewerLocalEditSynced() throws {
        var r = try acknowledged(2)
        r.surfaceCm2 = 74.55
        XCTAssertEqual(state(r).phase, .pending)
        let json = try XCTUnwrap(r.revisionJSON())
        r.cloudSync?.pending = LitePendingRevision(revision: 3, payloadJSON: json)
        XCTAssertEqual(state(r, syncing: true).phase, .syncing)
        XCTAssertTrue(state(r, syncing: true).title.contains("第 3 版"))
    }

    func testCapacityConsentWithdrawalAndMissingIdentityNeverOfferInvalidRetry() throws {
        var r = try acknowledged()
        r.surfaceCm2 = 78.02
        let now = Date(timeIntervalSince1970: 1000)
        r.cloudSync?.capacityNotBefore = now.addingTimeInterval(300)
        XCTAssertEqual(state(r, now: now).phase, .waiting)
        XCTAssertNil(state(r, now: now).retryTitle)
        XCTAssertNotNil(state(r, now: now.addingTimeInterval(301)).retryTitle)
        XCTAssertEqual(state(r, consent: false).phase, .localOnly)
        XCTAssertNil(state(r, consent: false).retryTitle)
        XCTAssertEqual(state(try acknowledged(), withdrawn: true).phase, .localOnly)
        XCTAssertNil(state(r, withdrawn: true).retryTitle)
        r.cloudSync = nil
        XCTAssertEqual(state(r).phase, .unavailable)
        XCTAssertNil(state(r).retryTitle)
    }

    @MainActor
    func testFailureRetryAndReceiptDriveRealStorePresentationWithoutDuplicateSend() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        var r = try acknowledged(1); r.surfaceCm2 = 78.02
        XCTAssertTrue(store.add(r))
        var requests = 0
        await store.syncRevision(recordID: r.id, consent: { true }) { _, pending in
            requests += 1
            XCTAssertEqual(self.state(store.records[0], syncing: true).phase, .syncing)
            XCTAssertEqual(pending.revision, 2)
            throw URLError(.notConnectedToInternet)
        }
        let failed = state(store.records[0])
        XCTAssertEqual(failed.phase, .failed); XCTAssertNotNil(failed.retryTitle)
        let bytes = store.records[0].cloudSync?.pending
        let restarted = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        await restarted.syncRevision(recordID: r.id, consent: { true }) { _, pending in
            requests += 1; XCTAssertEqual(pending, bytes)
        }
        XCTAssertEqual(state(restarted.records[0]).phase, .synced)
        XCTAssertTrue(state(restarted.records[0]).title.contains("第 2 版"))
        XCTAssertNil(state(restarted.records[0]).retryTitle)
        await restarted.syncRevision(recordID: r.id, consent: { true }) { _, _ in requests += 1 }
        XCTAssertEqual(requests, 2)
        XCTAssertEqual(restarted.records[0].imageName, "preserved.jpg")
        XCTAssertEqual(restarted.records[0].depthName, "preserved.depth")
    }

    func testAreaSummaryExplicitlyDistinguishesChangedMaskFromRoundedArea() {
        XCTAssertTrue(LiteContourChangeSummary(before: 80.358, after: 78.016).text.contains("80.36 → 78.02"))
        let same = LiteContourChangeSummary(before: 80.361, after: 80.363).text
        XCTAssertTrue(same.contains("圈選已修正")); XCTAssertTrue(same.contains("仍顯示"))
        XCTAssertFalse(same.contains("未修改"))
        let legacy = LiteContourChangeSummary(before: 80, after: 75, comparable: false).text
        XCTAssertFalse(legacy.contains("→")); XCTAssertTrue(legacy.contains("來源不同"))
    }

    func testEditorSessionUsesLatestRecordAndRejectsDuplicateOrLateCompletion() throws {
        var flow = LiteContourEditFlow()
        let first = try acknowledged()
        flow.begin(record: first, fallback: [])
        let session1 = try XCTUnwrap(flow.session)
        XCTAssertEqual(session1.polygons[0][0], [20,20])
        XCTAssertNotNil(flow.finish(session1.id))
        XCTAssertNil(flow.finish(session1.id))
        var second = first; second.polysJson = "[[[21,20],[81,20],[81,80],[21,80]]]"
        flow.begin(record: second, fallback: session1.polygons)
        let session2 = try XCTUnwrap(flow.session)
        XCTAssertNotEqual(session1.id, session2.id)
        XCTAssertEqual(session2.polygons[0][0], [21,20])
        XCTAssertNil(flow.finish(session1.id))
        XCTAssertEqual(flow.session?.id, session2.id)
        flow.cancel()
        XCTAssertNil(flow.finish(session2.id))
    }

    @MainActor
    func testThreeRevisionsLostReceiptAndRepeatedChecksReuseOneImageAndStableVersions() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        var store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        XCTAssertTrue(store.add(record()))
        var received: [Int: LitePendingRevision] = [:]
        var attempts: [Int] = []
        var loseReceipt = true
        let sender: (LiteCloudBinding, LitePendingRevision) async throws -> Void = { binding, pending in
            attempts.append(pending.revision)
            XCTAssertEqual(binding.imageID, "original")
            if let previous = received[pending.revision] { XCTAssertEqual(previous, pending) }
            received[pending.revision] = pending
            let payload = try JSONDecoder().decode(LiteRevisionPayload.self, from: Data(pending.payloadJSON.utf8))
            XCTAssertEqual(payload.image_w, 128)
            let keys = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(pending.payloadJSON.utf8)) as? [String: Any])
            XCTAssertNil(keys["image"]); XCTAssertNil(keys["depth_map"]); XCTAssertNil(keys["rgbd"])
            XCTAssertLessThan(pending.payloadJSON.utf8.count, 2048)
            if pending.revision == 2 && loseReceipt {
                loseReceipt = false
                throw URLError(.timedOut) // Server stored it, but its receipt did not arrive.
            }
        }
        await store.syncRevision(recordID: "synthetic", consent: { true }, send: sender)
        var r = store.records[0]; r.surfaceCm2 = 78.02
        r.polysJson = "[[[21,20],[80,20],[80,80],[20,80]]]"
        XCTAssertTrue(store.update(r))
        await store.syncRevision(recordID: r.id, consent: { true }, send: sender)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 1)
        store = LiteStore(fileURL: dir.appendingPathComponent("records.json"))
        await store.syncRevision(recordID: r.id, consent: { true }, send: sender)
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 2)
        r = store.records[0]; r.surfaceCm2 = 74.55
        r.polysJson = "[[[22,20],[80,20],[80,80],[20,80]]]"
        XCTAssertTrue(store.update(r))
        await store.syncRevision(recordID: r.id, consent: { true }, send: sender)
        for _ in 0..<20 { await store.syncRevision(recordID: r.id, consent: { true }, send: sender) }
        XCTAssertEqual(attempts, [1, 2, 2, 3])
        XCTAssertEqual(received.keys.sorted(), [1, 2, 3])
        XCTAssertEqual(store.records[0].cloudSync?.acknowledgedRevision, 3)
        XCTAssertEqual(store.records[0].imageName, "preserved.jpg")
        XCTAssertEqual(store.records[0].depthName, "preserved.depth")
        XCTAssertEqual(state(store.records[0]).phase, .synced)
    }

    @MainActor
    func testActionCardSnapshotsForSuccessProgressFailureAndUnchangedReturn() async throws {
        let good = try acknowledged(3)
        var failed = good; failed.surfaceCm2 = 73.2
        failed.cloudSync?.lastError = "連線中斷，本機修改已保留，可稍後重試。"
        let variants: [(String, LiteRevisionPresentation, String?)] = [
            ("synced", state(good), nil),
            ("syncing", state(failed, syncing: true), nil),
            ("failed", state(failed), nil),
            ("unchanged-return", state(good), "本次未新增圈選修改；原紀錄與同步狀態保持不變。")
        ]
        for (name, status, note) in variants {
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
            let view = NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        RoundedRectangle(cornerRadius: 12).fill(Color.pink.opacity(0.4))
                            .frame(height: 250).overlay(Text("合成標靶預覽"))
                        Text("LiDAR 表面積估算：74.55 cm²").font(.title3).bold()
                        Text("研究參考估算 · 保留原始影像與深度").foregroundStyle(.secondary)
                    }.padding()
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    LiteRevisionActionCard(status: status,
                        change: .init(before: 78.02, after: 74.55), note: note,
                        noteIsInformational: true, onEdit: {}, onRetry: {})
                }
                .navigationTitle("量測紀錄")
            }.environment(\.locale, Locale(identifier: "zh_TW"))
            let host = UIHostingController(rootView: view)
            window.rootViewController = host; window.makeKeyAndVisible()
            host.view.setNeedsLayout(); host.view.layoutIfNeeded()
            try await Task.sleep(nanoseconds: 150_000_000)
            let image = UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
                host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
            }
            let attachment = XCTAttachment(image: image)
            attachment.name = "lite-revision-\(name)"; attachment.lifetime = .keepAlways; add(attachment)
            window.isHidden = true; window.rootViewController = nil
        }
    }
}
