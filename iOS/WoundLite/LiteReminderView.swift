import SwiftUI

struct LiteReminderView: View {
    @StateObject private var model: LiteReminderModel
    @Environment(\.locale) private var locale
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @State private var time = Date()
    init(model: LiteReminderModel = .shared) { _model = StateObject(wrappedValue: model) }
    private var copy: LiteReminderCopy { LiteReminderCopy(locale: locale) }
    private var status: String {
        switch model.state {
        case .off: return "提醒已關閉。"
        case .scheduled: return "已設定每天提醒；時間以這部裝置的當地時間為準。"
        case .denied: return "系統通知未允許。請到 iOS 設定允許通知，再回來儲存提醒。"
        case .unconfirmed: return "提醒排程尚未確認。請檢查時間後重新儲存。"
        case .storageUnavailable: return "提醒設定無法保存或讀取。請解鎖後再試；原傷口紀錄不受影響。"
        }
    }
    var body: some View {
        Form {
            Section {
                Text(copy.text("依您的需要設定每天一次的紀錄提醒。這不是換藥、治療或就醫時程建議。"))
                Text(copy.text("只在這部裝置提醒，不需登入或研究同意，不上傳資料、不扣辨識額度。通知不包含傷口照片、部位或面積。"))
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section(copy.text("提醒時間")) {
                DatePicker(copy.text("每天"), selection: $time, displayedComponents: .hourAndMinute)
                    .accessibilityIdentifier("lite.reminder.time")
                Button(copy.text(model.setting.enabled ? "儲存提醒時間" : "啟用每天提醒")) {
                    let fields = Calendar.current.dateComponents([.hour, .minute], from: time)
                    Task { await model.save(hour: fields.hour ?? -1, minute: fields.minute ?? -1, language: copy.language) }
                }.accessibilityIdentifier("lite.reminder.save")
                if model.setting.enabled || model.state != .off {
                    Button(copy.text("關閉提醒"), role: .destructive) { Task { await model.disable() } }
                        .accessibilityIdentifier("lite.reminder.disable")
                }
            }.disabled(model.busy)
            Section(copy.text("狀態")) {
                if model.busy { ProgressView(copy.text("正在核對提醒…")) }
                Text(copy.text(status)).accessibilityIdentifier("lite.reminder.status")
                if model.state == .scheduled {
                    let confirmed = Calendar.current.date(bySettingHour: model.setting.hour, minute: model.setting.minute, second: 0, of: Date()) ?? time
                    LabeledContent(copy.text("已保存時間"), value: confirmed.formatted(date: .omitted, time: .shortened))
                }
                if model.state == .denied {
                    Button(copy.text("開啟 iOS 通知設定")) {
                        if let url = URL(string: UIApplication.openNotificationSettingsURLString) { openURL(url) }
                    }
                }
                Text(copy.text("專注模式、通知摘要及系統設定可能延後或隱藏提醒；請勿依賴通知安排必要照護。更換 App 語言後，可重新儲存以更新通知文字。"))
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle(copy.text("本機紀錄提醒"))
        .task {
            await model.refresh()
            time = Calendar.current.date(bySettingHour: model.setting.hour, minute: model.setting.minute, second: 0, of: Date()) ?? Date()
        }
        .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await model.refresh() } } }
    }
}
