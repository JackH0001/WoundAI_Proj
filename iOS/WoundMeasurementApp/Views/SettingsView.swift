import SwiftUI
import CoreData

struct SettingsView: View {
    @StateObject private var dataManager = DataManager.shared
    @State private var showingExportSheet = false
    @State private var showingClearDataAlert = false
    @State private var exportData: Data?
    
    // 設定選項
    @AppStorage("measurementUnit") private var measurementUnit = "cm"
    @AppStorage("autoSave") private var autoSave = true
    @AppStorage("highQualityMode") private var highQualityMode = false
    @AppStorage("enableNotifications") private var enableNotifications = true
    @AppStorage("darkMode") private var darkMode = false
    
    var body: some View {
        NavigationView {
            List {
                // 測量設定
                Section(header: Text("測量設定")) {
                    HStack {
                        Text("測量單位")
                        Spacer()
                        Picker("測量單位", selection: $measurementUnit) {
                            Text("公分 (cm)").tag("cm")
                            Text("英吋 (in)").tag("in")
                            Text("毫米 (mm)").tag("mm")
                        }
                        .pickerStyle(MenuPickerStyle())
                    }
                    
                    Toggle("高品質模式", isOn: $highQualityMode)
                        .onChange(of: highQualityMode) { newValue in
                            // 更新處理模組的品質設定
                            print("高品質模式: \(newValue)")
                        }
                }
                
                // 資料管理
                Section(header: Text("資料管理")) {
                    Toggle("自動儲存", isOn: $autoSave)
                    
                    HStack {
                        Text("已儲存記錄")
                        Spacer()
                        Text("\(dataManager.savedResults.count)")
                            .foregroundColor(.secondary)
                    }
                    
                    Button("匯出資料") {
                        exportData = dataManager.exportData()
                        showingExportSheet = true
                    }
                    .foregroundColor(.blue)
                    
                    Button("清除所有資料") {
                        showingClearDataAlert = true
                    }
                    .foregroundColor(.red)
                }
                
                // 通知設定
                Section(header: Text("通知設定")) {
                    Toggle("啟用通知", isOn: $enableNotifications)
                    
                    if enableNotifications {
                        NavigationLink("通知偏好設定") {
                            NotificationSettingsView()
                        }
                    }
                }
                
                // 外觀設定
                Section(header: Text("外觀設定")) {
                    Toggle("深色模式", isOn: $darkMode)
                        .onChange(of: darkMode) { newValue in
                            // 更新應用程式外觀
                            print("深色模式: \(newValue)")
                        }
                }
                
                // 關於
                Section(header: Text("關於")) {
                    HStack {
                        Text("版本")
                        Spacer()
                        Text("1.0.0")
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("建置版本")
                        Spacer()
                        Text("1")
                            .foregroundColor(.secondary)
                    }
                    
                    NavigationLink("隱私政策") {
                        PrivacyPolicyView()
                    }
                    
                    NavigationLink("使用條款") {
                        TermsOfServiceView()
                    }
                }
                
                // 統計資訊
                Section(header: Text("統計資訊")) {
                    let stats = dataManager.getStatistics()
                    
                    HStack {
                        Text("總記錄數")
                        Spacer()
                        Text("\(stats.totalRecords)")
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("成功記錄")
                        Spacer()
                        Text("\(stats.successfulRecords)")
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("成功率")
                        Spacer()
                        Text("\(Int(stats.successRate * 100))%")
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("平均面積")
                        Spacer()
                        Text(String(format: "%.2f %@²", stats.averageArea, measurementUnit))
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("平均體積")
                        Spacer()
                        Text(String(format: "%.2f %@³", stats.averageVolume, measurementUnit))
                            .foregroundColor(.secondary)
                    }
                }
            }
            .navigationTitle("設定")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingExportSheet) {
                if let data = exportData {
                    ShareSheet(items: [data])
                }
            }
            .alert("清除資料", isPresented: $showingClearDataAlert) {
                Button("取消", role: .cancel) { }
                Button("清除", role: .destructive) {
                    clearAllData()
                }
            } message: {
                Text("此操作將永久刪除所有儲存的測量記錄，無法復原。")
            }
        }
    }
    
    private func clearAllData() {
        // 清除所有 Core Data 記錄
        let context = dataManager.container.viewContext
        let fetchRequest: NSFetchRequest<NSFetchRequestResult> = WoundRecord.fetchRequest()
        let deleteRequest = NSBatchDeleteRequest(fetchRequest: fetchRequest)
        
        do {
            try context.execute(deleteRequest)
            try context.save()
            dataManager.fetchSavedResults()
        } catch {
            print("清除資料失敗: \(error.localizedDescription)")
        }
    }
}

// MARK: - 通知設定視圖

struct NotificationSettingsView: View {
    @AppStorage("measurementCompleteNotification") private var measurementCompleteNotification = true
    @AppStorage("dailyReminder") private var dailyReminder = false
    @AppStorage("weeklyReport") private var weeklyReport = false
    
    var body: some View {
        List {
            Section(header: Text("測量通知")) {
                Toggle("測量完成通知", isOn: $measurementCompleteNotification)
                Toggle("每日提醒", isOn: $dailyReminder)
                Toggle("每週報告", isOn: $weeklyReport)
            }
            
            Section(footer: Text("通知將在測量完成或設定的時間發送")) {
                Text("通知設定說明")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .navigationTitle("通知設定")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 隱私政策視圖

struct PrivacyPolicyView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("隱私政策")
                    .font(.title)
                    .fontWeight(.bold)
                
                Text("最後更新: 2024年1月")
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Group {
                    Text("資料收集")
                        .font(.headline)
                    Text("本應用程式僅收集必要的傷口測量資料，所有資料均儲存在您的裝置上，不會上傳至外部伺服器。")
                    
                    Text("資料使用")
                        .font(.headline)
                    Text("收集的資料僅用於傷口測量和分析功能，不會用於其他商業用途。")
                    
                    Text("資料安全")
                        .font(.headline)
                    Text("所有資料均使用裝置內建的安全機制進行保護，確保您的隱私安全。")
                    
                    Text("聯絡我們")
                        .font(.headline)
                    Text("如有任何隱私相關問題，請透過應用程式內的回饋功能與我們聯絡。")
                }
            }
            .padding()
        }
        .navigationTitle("隱私政策")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 使用條款視圖

struct TermsOfServiceView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("使用條款")
                    .font(.title)
                    .fontWeight(.bold)
                
                Text("最後更新: 2024年1月")
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Group {
                    Text("使用授權")
                        .font(.headline)
                    Text("本應用程式僅供醫療專業人員使用，使用者應具備相關專業知識。")
                    
                    Text("免責聲明")
                        .font(.headline)
                    Text("本應用程式提供的測量結果僅供參考，不應作為醫療診斷的唯一依據。")
                    
                    Text("責任限制")
                        .font(.headline)
                    Text("開發者不對使用本應用程式造成的任何直接或間接損失承擔責任。")
                    
                    Text("智慧財產權")
                        .font(.headline)
                    Text("本應用程式的所有智慧財產權均歸開發者所有。")
                }
            }
            .padding()
        }
        .navigationTitle("使用條款")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 分享表單已在AnnotationView.swift中定義 