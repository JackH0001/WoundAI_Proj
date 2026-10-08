import XCTest
import SwiftUI
@testable import WoundLite

final class LiteRevisionSyncTests: XCTestCase {
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
        XCTAssertTrue(store.records[0].cloudSyncDescription.contains("未保存"))
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

}
