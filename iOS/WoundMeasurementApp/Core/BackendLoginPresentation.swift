import Foundation

struct BackendLoginPresentation {
    let entryReady: Bool
    let verified: Bool
    let busy: Bool
    let saved: Bool
    var showLogout: Bool { entryReady && verified }
    var status: String {
        if !entryReady { return "WoundAI：尚未登入，請先完成 Google 機構門禁" }
        if verified { return busy ? "WoundAI：已登入，正在讀取雲端狀態…" : "WoundAI：已登入" }
        if busy { return "WoundAI：正在驗證登入…" }
        return saved ? "WoundAI：帳密已儲存，尚未驗證登入" : "WoundAI：尚未登入"
    }
    var button: String {
        if busy { return verified ? "正在讀取雲端狀態…" : "正在登入…" }
        return verified && entryReady ? "重新驗證連線" : "儲存並登入"
    }
    static func mayReusePassword(inputUser: String, savedUser: String, inputURL: String, savedURL: String) -> Bool {
        inputUser == savedUser && BackendClient.normalize(inputURL) == BackendClient.normalize(savedURL)
    }
}
