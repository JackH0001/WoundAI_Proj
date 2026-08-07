import SwiftUI

@main
struct WoundMeasurementApp: App {
    let dataManager = DataManager.shared
    @State private var showDataCleanupAlert = false
    @StateObject private var complianceManager = MedicalComplianceManager.shared
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(\.managedObjectContext, dataManager.container.viewContext)
                .environmentObject(dataManager)
                .handleErrors()
                .onAppear {
                    // 檢查是否需要清理資料
                    checkForDataCleanup()
                    complianceManager.showDisclaimerIfNeeded()
                }
                .alert("清理舊資料", isPresented: $showDataCleanupAlert) {
                    Button("清理全部") {
                        dataManager.clearAllData()
                        print("✅ 應用啟動時清理所有資料完成")
                    }
                    Button("清理30天前") {
                        dataManager.clearOldData(olderThanDays: 30)
                        print("✅ 應用啟動時清理30天前資料完成")
                    }
                    Button("暫不清理", role: .cancel) { }
                } message: {
                    let info = dataManager.getStorageInfo()
                    Text("目前有 \(info.recordCount) 筆記錄，佔用 \(info.storageSize) 空間。建議定期清理以提升效能。")
                }
                .sheet(isPresented: $complianceManager.showingDisclaimer) {
                    MedicalDisclaimerView(complianceManager: complianceManager)
                }
        }
    }
    
    private func checkForDataCleanup() {
        let info = dataManager.getStorageInfo()
        // 如果記錄數超過50筆或啟動時有問題，提示清理
        if info.recordCount > 50 || dataManager.errorMessage != nil {
            showDataCleanupAlert = true
        }
    }
}
