import Foundation
import CoreFoundation

/// Transport scheduling only: never changes the immutable research payload or its receipt.
struct ResearchCapacityWait: Error, LocalizedError {
    let notBefore: Date
    var errorDescription: String? { "後端容量暫滿或服務暫不可用；請依畫面上的待傳狀態稍候再試。" }

    static func check(_ response: HTTPURLResponse, data: Data, now: Date = Date()) throws {
        guard [429, 503].contains(response.statusCode) else { return }
        throw ResearchCapacityWait(notBefore: deadline(retryAfter: response.value(forHTTPHeaderField: "Retry-After"),
            serverDate: response.value(forHTTPHeaderField: "Date"), data: data, now: now))
    }

    static func deadline(retryAfter: String?, serverDate: String?, data: Data,
                         now: Date, minimum: TimeInterval = 60, jitter: TimeInterval = Double.random(in: 0...5)) -> Date {
        func number(_ text: String?) -> Double? {
            guard let text, let n = Double(text.trimmingCharacters(in: .whitespacesAndNewlines)),
                  n.isFinite, n >= 0 else { return nil }; return n
        }
        func date(_ text: String?) -> Date? {
            guard let text else { return nil }
            let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX")
            f.timeZone = TimeZone(secondsFromGMT: 0); f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
            f.isLenient = false; return f.date(from: text)
        }
        var delay = max(0, minimum)
        if let n = number(retryAfter) { delay = max(delay, n) }
        else if let d = date(retryAfter) { delay = max(delay, d.timeIntervalSince(date(serverDate) ?? now)) }
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let n = obj["retry_after"] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
           n.doubleValue.isFinite, n.doubleValue >= 0 { delay = max(delay, n.doubleValue) }
        // An unrepresentable delay stays blocked, never wraps into an immediate retry.
        return min(Date.distantFuture, now.addingTimeInterval(min(delay, Date.distantFuture.timeIntervalSince(now)) + max(0, jitter)))
    }
}
