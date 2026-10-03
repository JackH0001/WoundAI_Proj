import XCTest
import UIKit
@testable import WoundMeasurementApp

final class ImageLoadTests: XCTestCase {
    private func fixture(_ store: LocalImageStore) throws -> String {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 2048, height: 1536), format: format).image { c in
            UIColor.systemTeal.setFill()
            c.fill(CGRect(x: 0, y: 0, width: 2048, height: 1536))
        }
        let jpeg = try XCTUnwrap(image.jpegData(compressionQuality: 0.9))
        // Surface Keychain/environment errors rather than a misleading nil-image failure.
        _ = try PhiCrypto.encryptBytes(jpeg)
        return try XCTUnwrap(store.save(jpeg: jpeg))
    }

    func testThumbnailIsBoundedButFullImageKeepsPolygonSpace() async throws {
        let store = LocalImageStore(), loader = ImageLoadQueue()
        let name = try fixture(store)
        defer { store.delete(name) }
        let thumb = await loader.thumbnail(store: store, name: name, maxPixel: 160)
        XCTAssertTrue(thumb.exists)
        let cg = try XCTUnwrap(thumb.image?.cgImage)
        XCTAssertEqual(cg.width, 160)
        XCTAssertEqual(cg.height, 120)
        let full = await loader.fullImage(store: store, name: name)
        XCTAssertEqual(full?.cgImage?.width, 2048)
        XCTAssertEqual(full?.cgImage?.height, 1536)
    }

    func testCancelledCallerDoesNotDecodeExistingImage() async throws {
        let store = LocalImageStore(), loader = ImageLoadQueue()
        let name = try fixture(store)
        defer { store.delete(name) }
        let task = Task { () -> Bool in
            withUnsafeCurrentTask { $0?.cancel() }
            let thumbnail = await loader.thumbnail(store: store, name: name, maxPixel: 160)
            let full = await loader.fullImage(store: store, name: name)
            return thumbnail.image == nil && full == nil
        }
        let skipped = await task.value
        XCTAssertTrue(skipped)
    }

    func testDeletedImageIsNotReturnedFromAStaleCache() async throws {
        let store = LocalImageStore(), loader = ImageLoadQueue()
        let name = try fixture(store)
        defer { store.delete(name) }
        let first = await loader.thumbnail(store: store, name: name, maxPixel: 160)
        XCTAssertNotNil(first.image)
        store.delete(name)
        let removed = await loader.thumbnail(store: store, name: name, maxPixel: 160)
        XCTAssertFalse(removed.exists)
        XCTAssertNil(removed.image)
    }

    func testRapidConcurrentRequestsOnlyReturnSmallThumbnails() async throws {
        let store = LocalImageStore(), loader = ImageLoadQueue()
        let name = try fixture(store)
        defer { store.delete(name) }
        let sizes = await withTaskGroup(of: Int.self) { group in
            for _ in 0..<40 {
                group.addTask {
                    let r = await loader.thumbnail(store: store, name: name, maxPixel: 160)
                    return r.image?.cgImage?.width ?? 0
                }
            }
            var values: [Int] = []
            for await v in group { values.append(v) }
            return values
        }
        XCTAssertEqual(sizes.count, 40)
        XCTAssertTrue(sizes.allSatisfy { $0 == 160 })
    }
}
