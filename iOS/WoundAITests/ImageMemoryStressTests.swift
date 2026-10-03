import XCTest
import UIKit
import Darwin
@testable import WoundMeasurementApp

/// Component stress only. Camera/LiDAR, SwiftUI navigation and backgrounding
/// still require the separate device acceptance procedure.
final class ImageMemoryStressTests: XCTestCase {
    private func footprint() throws -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        XCTAssertEqual(result, KERN_SUCCESS)
        guard result == KERN_SUCCESS else { throw NSError(domain: "task_info", code: Int(result)) }
        return info.phys_footprint
    }

    // Force lazy UIImage decoding, as displaying a detail image would do.
    private func drawFull(_ image: UIImage) throws -> UInt64 {
        try autoreleasepool {
            let cg = try XCTUnwrap(image.cgImage)
            let context = try XCTUnwrap(CGContext(data: nil, width: cg.width, height: cg.height,
                bitsPerComponent: 8, bytesPerRow: cg.width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
            context.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
            return try footprint()
        }
    }

    func testHundredEncryptedImagesAndFiftyDetailDecodesStayBounded() async throws {
        let store = LocalImageStore(), loader = ImageLoadQueue()
        var names: [String] = []
        // Delete only UUID files created by this test, never the user's records.
        defer { names.forEach { store.delete($0) } }
        let initial = try footprint()
        let jpeg: Data = try autoreleasepool {
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            let image = UIGraphicsImageRenderer(size: CGSize(width: 4032, height: 3024), format: format).image { c in
                for column in 0..<16 {
                    UIColor(hue: CGFloat(column) / 16, saturation: 0.7, brightness: 0.8, alpha: 1).setFill()
                    c.fill(CGRect(x: column * 252, y: 0, width: 252, height: 3024))
                }
            }
            return try XCTUnwrap(image.jpegData(compressionQuality: 0.9))
        }
        for _ in 0..<100 { names.append(try XCTUnwrap(store.save(jpeg: jpeg))) }
        var sampledPeak = try footprint()
        var settled: [UInt64] = []
        for round in 0..<5 {
            // A history burst; task results retain dimensions, not decoded images.
            let widths = await withTaskGroup(of: Int.self) { group in
                for name in names {
                    group.addTask {
                        let result = await loader.thumbnail(store: store, name: name, maxPixel: 160)
                        return result.image?.cgImage?.width ?? 0
                    }
                }
                var values: [Int] = []
                for await width in group { values.append(width) }
                return values
            }
            XCTAssertEqual(widths.count, 100)
            XCTAssertTrue(widths.allSatisfy { $0 == 160 })
            for index in 0..<10 {
                let image = await loader.fullImage(store: store, name: names[round * 10 + index])
                sampledPeak = max(sampledPeak, try drawFull(XCTUnwrap(image)))
            }
            settled.append(try footprint())
        }
        let growth = Int64(settled.last!) - Int64(settled.first!)
        // A retained 12 MP image is ~46.5 MiB. Catch sustained retention across
        // forty further detail visits while allowing allocator/framework warmup.
        XCTAssertLessThan(growth, 64 * 1024 * 1024, "Post-warmup image memory kept growing")
        let evidence: [String: Any] = ["initial_bytes": initial, "sampled_peak_bytes": sampledPeak,
            "settled_bytes": settled, "post_warmup_growth_bytes": growth,
            "encrypted_images": 100, "thumbnail_requests": 500, "full_decodes": 50,
            "image_width": 4032, "image_height": 3024,
            "scope": "component only; sampled physical footprint, not continuous peak or UI jetsam acceptance"]
        let data = try JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys])
        print("WOUNDAI_MEMORY_STRESS " + String(decoding: data, as: UTF8.self))
        let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
        attachment.name = "image-memory-stress.json"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
