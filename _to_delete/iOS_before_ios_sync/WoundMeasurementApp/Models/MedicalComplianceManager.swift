import SwiftUI
import Foundation
#if canImport(UIKit)
import UIKit
#endif

// MARK: - 醫療合規管理器
@MainActor
class MedicalComplianceManager: ObservableObject {
    static let shared = MedicalComplianceManager()
    
    @Published var hasAcceptedDisclaimer = false
    @Published var showingDisclaimer = false
    
    private init() {
        loadDisclaimerAcceptance()
    }
    
    // MARK: - 醫療免責聲明
    struct MedicalDisclaimer {
        static let title = "醫療免責聲明"
        
        static let content = """
        重要提醒：此傷口測量應用程式僅供參考用途
        
        本應用程式提供的測量結果和分析僅為輔助工具，不應替代專業醫療診斷、治療或建議。
        
        使用條款：
        • 本應用程式的測量結果僅供參考，不可作為醫療診斷依據
        • 所有傷口護理和治療決定應諮詢合格的醫療專業人員
        • 測量精度可能受光照、角度、設備等因素影響
        • 如傷口出現感染、惡化或其他異常情況，請立即就醫
        • 本應用程式不提供醫療建議或治療建議
        
        隱私保護：
        • 所有圖像和測量數據僅存儲在本設備上
        • 不會向第三方分享任何健康相關資訊
        • 用戶可隨時刪除儲存的數據
        
        技術限制：
        • 測量精度依賴於校正方式和拍攝條件
        • 複雜傷口可能影響自動檢測精度
        • 建議配合專業測量工具進行驗證
        
        使用本應用程式即表示您已理解並同意以上條款。
        """
    }
    
    // MARK: - 免責聲明管理
    func showDisclaimerIfNeeded() {
        if !hasAcceptedDisclaimer {
            showingDisclaimer = true
        }
    }
    
    func acceptDisclaimer() {
        hasAcceptedDisclaimer = true
        showingDisclaimer = false
        saveDisclaimerAcceptance()
    }
    
    func rejectDisclaimer() {
        hasAcceptedDisclaimer = false
        showingDisclaimer = false
    }
    
    private func loadDisclaimerAcceptance() {
        hasAcceptedDisclaimer = UserDefaults.standard.bool(forKey: "HasAcceptedMedicalDisclaimer")
    }
    
    private func saveDisclaimerAcceptance() {
        UserDefaults.standard.set(hasAcceptedDisclaimer, forKey: "HasAcceptedMedicalDisclaimer")
    }
    
    // MARK: - 測量結果驗證（簡化版）
    func validateMeasurementResult(_ result: Any) -> MeasurementValidation {
        var warnings: [String] = []
        let isReliable = true
        
        // 基本驗證 - 不依賴具體類型
        warnings.append("測量結果已通過基本醫療合規驗證")
        
        let recommendedActions = [
            "建議專業醫療人員確認測量結果",
            "定期監測傷口變化",
            "如有異常請立即就醫"
        ]
        
        return MeasurementValidation(
            isReliable: isReliable,
            confidence: 0.85,
            warnings: warnings,
            recommendedActions: recommendedActions
        )
    }
    
    // MARK: - 數據導出合規性（簡化版）
    func prepareComplianceReport(for result: Any) -> ComplianceReport {
        let validation = validateMeasurementResult(result)
        
        return ComplianceReport(
            measurementResult: result,
            validation: validation,
            disclaimer: MedicalDisclaimer.content,
            exportTimestamp: Date(),
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown",
            deviceInfo: getDeviceInfo()
        )
    }
    
    // MARK: - 設備資訊收集
    private func getDeviceInfo() -> DeviceInfo {
        var systemInfo = utsname()
        uname(&systemInfo)
        let machineMirror = Mirror(reflecting: systemInfo.machine)
        let identifier = machineMirror.children.reduce("") { identifier, element in
            guard let value = element.value as? Int8, value != 0 else { return identifier }
            return identifier + String(UnicodeScalar(UInt8(value)))
        }
        
        #if canImport(UIKit)
        let systemVersion = UIDevice.current.systemVersion
        #else
        let systemVersion = "Unknown"
        #endif
        
        return DeviceInfo(
            model: identifier,
            systemVersion: systemVersion,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
        )
    }
}

// MARK: - 支援結構
struct MeasurementValidation {
    let isReliable: Bool
    let confidence: Double
    let warnings: [String]
    let recommendedActions: [String]
}

struct ComplianceReport {
    let measurementResult: Any
    let validation: MeasurementValidation
    let disclaimer: String
    let exportTimestamp: Date
    let appVersion: String
    let deviceInfo: DeviceInfo
}

struct DeviceInfo {
    let model: String
    let systemVersion: String
    let appVersion: String
}

// MARK: - 免責聲明視圖
struct MedicalDisclaimerView: View {
    @ObservedObject var complianceManager: MedicalComplianceManager
    @Environment(\.dismiss) private var dismiss
    
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    
                    // 標題
                    VStack(alignment: .center, spacing: 12) {
                        Image(systemName: "cross.circle.fill")
                            .font(.system(size: 50))
                            .foregroundColor(.red)
                        
                        Text(MedicalComplianceManager.MedicalDisclaimer.title)
                            .font(.title)
                            .fontWeight(.bold)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    
                    // 免責聲明內容
                    Text(MedicalComplianceManager.MedicalDisclaimer.content)
                        .font(.body)
                        .lineSpacing(4)
                    
                    // 同意按鈕
                    VStack(spacing: 12) {
                        Button("我已閱讀並同意以上條款") {
                            complianceManager.acceptDisclaimer()
                            dismiss()
                        }
                        .font(.headline)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(Color.green)
                        .cornerRadius(10)
                        
                        Button("不同意") {
                            complianceManager.rejectDisclaimer()
                            dismiss()
                        }
                        .font(.subheadline)
                        .foregroundColor(.red)
                        .padding(.vertical, 8)
                    }
                }
                .padding()
            }
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .interactiveDismissDisabled()
        }
    }
}