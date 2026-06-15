import Foundation
import CoreData
import SwiftUI

/// 統一的資料管理層
class DataManager: ObservableObject {
    static let shared = DataManager()
    
    let container: NSPersistentContainer
    
    @Published var savedResults: [WoundRecord] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    
    private init() {
        print("🔍 開始初始化 CoreData...")
        
        // 列出 Bundle 中的所有資源來調試
        let bundlePath = Bundle.main.bundlePath
        print("📦 Bundle 路徑: \(bundlePath)")
        let bundleContents = try? FileManager.default.contentsOfDirectory(atPath: bundlePath)
        print("📦 Bundle 內容: \(bundleContents?.prefix(10) ?? [])")
        
        // 檢查編譯後的模型文件
        if let momdURL = Bundle.main.url(forResource: "WoundMeasurementModel", withExtension: "momd") {
            print("✅ 找到編譯後的模型: \(momdURL)")
            
            if let managedObjectModel = NSManagedObjectModel(contentsOf: momdURL) {
                print("✅ NSManagedObjectModel 創建成功")
                container = NSPersistentContainer(name: "WoundMeasurementModel", managedObjectModel: managedObjectModel)
            } else {
                print("❌ 無法從 momd 創建 NSManagedObjectModel，使用默認方式")
                container = NSPersistentContainer(name: "WoundMeasurementModel")
            }
        } else {
            print("⚠️ 未找到編譯後的 momd 文件，使用默認方式")
            container = NSPersistentContainer(name: "WoundMeasurementModel")
        }
        
        // 配置持久存儲
        if let storeDescription = container.persistentStoreDescriptions.first {
            storeDescription.shouldInferMappingModelAutomatically = true
            storeDescription.shouldMigrateStoreAutomatically = true
            print("🔧 持久存儲配置完成")
        }
        
        container.loadPersistentStores { description, error in
            if let error = error {
                print("❌ Core Data 載入失敗: \(error.localizedDescription)")
                print("❌ 錯誤詳情: \(error)")
                if let nsError = error as NSError? {
                    print("❌ NSError 代碼: \(nsError.code)")
                    print("❌ NSError 域: \(nsError.domain)")
                    print("❌ NSError 用戶信息: \(nsError.userInfo)")
                }
                DispatchQueue.main.async {
                    self.errorMessage = "資料庫載入失敗: \(error.localizedDescription)"
                }
            } else {
                print("✅ Core Data 載入成功!")
                print("   - 存儲類型: \(description.type)")
                print("   - 存儲URL: \(description.url?.absoluteString ?? "無")")
                
                // 在載入完成後再配置與讀取，避免在主執行緒阻塞
                self.container.viewContext.automaticallyMergesChangesFromParent = true
                self.container.viewContext.mergePolicy = NSMergeByPropertyObjectTrumpMergePolicy
                
                // 驗證實體是否正確載入
                let entityDescriptions = self.container.managedObjectModel.entities
                print("📋 載入的實體: \(entityDescriptions.map { $0.name ?? "無名稱" })")
                
                // 初次讀取資料
                self.fetchSavedResults()
            }
        }
    }
    
    // MARK: - Core Data 操作
    
    func saveContext() {
        let context = container.viewContext
        
        if context.hasChanges {
            do {
                try context.save()
                fetchSavedResults()
            } catch {
                print("儲存失敗: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.errorMessage = "儲存失敗"
                }
            }
        }
    }
    
    func fetchSavedResults() {
        let request: NSFetchRequest<WoundRecord> = WoundRecord.fetchRequest()
        request.sortDescriptors = [NSSortDescriptor(keyPath: \WoundRecord.date, ascending: false)]
        request.fetchBatchSize = 50
        request.includesPropertyValues = true
        request.returnsObjectsAsFaults = true
        
        do {
            let results = try container.viewContext.fetch(request)
            DispatchQueue.main.async {
                self.savedResults = results
            }
        } catch {
            print("讀取失敗: \(error.localizedDescription)")
            DispatchQueue.main.async {
                self.errorMessage = "讀取失敗"
            }
        }
    }
    
    func saveWoundResult(_ result: WoundMeasurementResult) {
        let context = container.viewContext
        let record = WoundRecord(context: context)
        
        record.id = UUID()
        record.date = result.timestamp
        record.area = result.area ?? 0
        record.volume = result.volume ?? 0
        record.acuteScore = result.classification?.acuteScore ?? 0
        record.chronicScore = result.classification?.chronicScore ?? 0
        record.confidence = result.classification?.confidence ?? 0
        record.errorMessage = result.error
        
        // 保存圖像數據
        if let originalImage = result.originalImage {
            record.imageData = originalImage.jpegData(compressionQuality: 0.8)
        }
        
        saveContext()
    }
    
    func deleteWoundRecord(_ record: WoundRecord) {
        let context = container.viewContext
        context.delete(record)
        saveContext()
    }
    
    func getAllWoundRecords() -> [WoundRecord] {
        let request: NSFetchRequest<WoundRecord> = WoundRecord.fetchRequest()
        request.sortDescriptors = [NSSortDescriptor(keyPath: \WoundRecord.date, ascending: false)]
        request.fetchBatchSize = 50
        request.includesPropertyValues = true
        request.returnsObjectsAsFaults = true
        
        do {
            return try container.viewContext.fetch(request)
        } catch {
            print("讀取所有記錄失敗: \(error.localizedDescription)")
            return []
        }
    }
    
    // MARK: - 統計功能
    
    func getStatistics() -> WoundStatistics {
        let totalRecords = savedResults.count
        let successfulRecords = savedResults.filter { $0.errorMessage == nil }.count
        let averageArea = savedResults.compactMap { $0.area }.reduce(0, +) / Double(max(totalRecords, 1))
        let averageVolume = savedResults.compactMap { $0.volume }.reduce(0, +) / Double(max(totalRecords, 1))
        
        return WoundStatistics(
            totalRecords: totalRecords,
            successfulRecords: successfulRecords,
            successRate: Double(successfulRecords) / Double(max(totalRecords, 1)),
            averageArea: averageArea,
            averageVolume: averageVolume
        )
    }
    
    // MARK: - 資料清理功能
    
    func clearAllData() {
        print("🗑️ 開始清理所有Core Data資料...")
        let context = container.viewContext
        
        // 清理所有 WoundRecord
        let request: NSFetchRequest<NSFetchRequestResult> = WoundRecord.fetchRequest()
        let deleteRequest = NSBatchDeleteRequest(fetchRequest: request)
        
        do {
            try container.persistentStoreCoordinator.execute(deleteRequest, with: context)
            try context.save()
            print("✅ 所有WoundRecord資料已清理")
            
            // 更新UI
            DispatchQueue.main.async {
                self.savedResults.removeAll()
            }
            
            fetchSavedResults()
        } catch {
            print("❌ 清理資料失敗: \(error)")
            DispatchQueue.main.async {
                self.errorMessage = "清理資料失敗: \(error.localizedDescription)"
            }
        }
    }
    
    func clearOldData(olderThanDays days: Int = 30) {
        print("🗑️ 清理 \(days) 天前的舊資料...")
        let context = container.viewContext
        let cutoffDate = Calendar.current.date(byAdding: .day, value: -days, to: Date()) ?? Date()
        
        let request: NSFetchRequest<WoundRecord> = WoundRecord.fetchRequest()
        request.predicate = NSPredicate(format: "date < %@", cutoffDate as NSDate)
        
        do {
            let oldRecords = try context.fetch(request)
            print("🗑️ 找到 \(oldRecords.count) 筆舊資料待清理")
            
            for record in oldRecords {
                context.delete(record)
            }
            
            try context.save()
            print("✅ 已清理 \(oldRecords.count) 筆舊資料")
            
            fetchSavedResults()
        } catch {
            print("❌ 清理舊資料失敗: \(error)")
            DispatchQueue.main.async {
                self.errorMessage = "清理舊資料失敗: \(error.localizedDescription)"
            }
        }
    }
    
    func getStorageInfo() -> (recordCount: Int, storageSize: String) {
        let recordCount = savedResults.count
        
        // 計算存儲大小
        var totalSize: Int64 = 0
        for record in savedResults {
            if let imageData = record.imageData {
                totalSize += Int64(imageData.count)
            }
        }
        
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = [.useMB, .useKB, .useBytes]
        formatter.countStyle = .file
        let sizeString = formatter.string(fromByteCount: totalSize)
        
        return (recordCount, sizeString)
    }
    
    // MARK: - 匯出功能
    
    func exportData() -> Data? {
        let exportData = savedResults.map { record in
            [
                "id": record.id.uuidString,
                "timestamp": record.date.timeIntervalSince1970,
                "area": record.area,
                "volume": record.volume,
                "acuteScore": record.acuteScore,
                "chronicScore": record.chronicScore,
                "confidence": record.confidence,
                "errorMessage": record.errorMessage ?? ""
            ]
        }
        
        return try? JSONSerialization.data(withJSONObject: exportData, options: .prettyPrinted)
    }
}

// MARK: - 統計結構

struct WoundStatistics {
    let totalRecords: Int
    let successfulRecords: Int
    let successRate: Double
    let averageArea: Double
    let averageVolume: Double
} 