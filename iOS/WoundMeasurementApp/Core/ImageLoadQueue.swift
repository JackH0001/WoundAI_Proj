import Foundation
import UIKit

/// Shared, serial decrypt/decode lane for history and review views in both apps.
/// No full-image cache. Queued work checks cancellation before allocating; each
/// synchronous operation drains its autorelease pool before the next starts.
actor ImageLoadQueue {
    static let shared = ImageLoadQueue()

    func thumbnail(store: LocalImageStore, name: String, maxPixel: Int) -> (exists: Bool, image: UIImage?) {
        guard !Task.isCancelled else { return (false, nil) }
        return autoreleasepool {
            let exists = store.exists(name)
            return (exists, exists ? store.loadThumbnail(name, maxPixel: maxPixel) : nil)
        }
    }

    func fullImage(store: LocalImageStore, name: String) -> UIImage? {
        guard !Task.isCancelled else { return nil }
        // Keep original dimensions: polygon coordinates must not be rescaled.
        return autoreleasepool { store.loadFull(name) }
    }
}
