import SwiftUI

struct CloudUploadView: View {
    @ObservedObject var annotationManager: WoundAnnotationManager
    @ObservedObject var cloudService: CloudAPIService
    let selectedImage: UIImage?
    let doctorId: String
    let patientId: String?
    let onUploadComplete: (CloudUploadResponse) -> Void
    
    @State private var isUploading = false
    @State private var errorMessage = ""
    @State private var showingError = false
    @Environment(\.dismiss) private var dismiss
    
    var body: some View {
        NavigationView {
            VStack(spacing: 20) {
                
                // 上傳狀態顯示
                if cloudService.isUploading {
                    VStack(spacing: 15) {
                        ProgressView(value: cloudService.uploadProgress)
                            .progressViewStyle(LinearProgressViewStyle())
                            .scaleEffect(x: 1, y: 2, anchor: .center)
                        
                        Text("正在上傳標註資料至雲端...")
                            .font(.headline)
                            .foregroundColor(.primary)
                        
                        Text("\(Int(cloudService.uploadProgress * 100))%")
                            .font(.title2)
                            .fontWeight(.bold)
                            .foregroundColor(.blue)
                    }
                    .padding()
                    .background(Color.gray.opacity(0.1))
                    .cornerRadius(12)
                    
                } else {
                    // 上傳前確認資訊
                    VStack(alignment: .leading, spacing: 15) {
                        
                        Text("上傳確認")
                            .font(.title2)
                            .fontWeight(.bold)
                        
                        // 醫師資訊
                        VStack(alignment: .leading, spacing: 8) {
                            Label("醫師ID", systemImage: "person.badge.key")
                                .font(.headline)
                            Text(doctorId)
                                .padding(.leading, 25)
                                .foregroundColor(.secondary)
                        }
                        
                        // 病患資訊
                        if let patientId = patientId, !patientId.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Label("病患ID", systemImage: "person")
                                    .font(.headline)
                                Text(patientId)
                                    .padding(.leading, 25)
                                    .foregroundColor(.secondary)
                            }
                        }
                        
                        // 標註資訊
                        VStack(alignment: .leading, spacing: 8) {
                            Label("標註資料", systemImage: "doc.text")
                                .font(.headline)
                            
                            if let annotation = annotationManager.currentAnnotation {
                                Text("包含 \(annotation.annotations.count) 個標註項目")
                                    .padding(.leading, 25)
                                    .foregroundColor(.secondary)
                                
                                ForEach(AnnotationType.allCases, id: \.self) { type in
                                    let count = annotation.annotations.filter { $0.type == type }.count
                                    if count > 0 {
                                        Text("• \(type.displayName): \(count) 個")
                                            .font(.caption)
                                            .padding(.leading, 35)
                                            .foregroundColor(.secondary)
                                    }
                                }
                            } else {
                                Text("尚無標註資料")
                                    .padding(.leading, 25)
                                    .foregroundColor(.orange)
                            }
                        }
                        
                        // 影像資訊
                        VStack(alignment: .leading, spacing: 8) {
                            Label("影像檔案", systemImage: "photo")
                                .font(.headline)
                            
                            if let image = selectedImage {
                                Text("尺寸: \(Int(image.size.width)) x \(Int(image.size.height))")
                                    .padding(.leading, 25)
                                    .foregroundColor(.secondary)
                            } else {
                                Text("尚未選擇影像")
                                    .padding(.leading, 25)
                                    .foregroundColor(.orange)
                            }
                        }
                        
                        Divider()
                        
                        // 重要提醒
                        VStack(alignment: .leading, spacing: 8) {
                            Label("重要提醒", systemImage: "exclamationmark.triangle")
                                .font(.headline)
                                .foregroundColor(.orange)
                            
                            Text("• 上傳的資料將用於AI模型訓練")
                                .font(.caption)
                                .padding(.leading, 25)
                                .foregroundColor(.secondary)
                            
                            Text("• 請確保已獲得適當的醫療資料使用授權")
                                .font(.caption)
                                .padding(.leading, 25)
                                .foregroundColor(.secondary)
                            
                            Text("• 上傳過程中請保持網路連線")
                                .font(.caption)
                                .padding(.leading, 25)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding()
                    .background(Color.gray.opacity(0.05))
                    .cornerRadius(12)
                    
                    Spacer()
                    
                    // 上傳按鈕
                    Button(action: uploadAnnotation) {
                        HStack {
                            Image(systemName: "icloud.and.arrow.up")
                            Text("確認上傳至雲端")
                        }
                        .font(.headline)
                        .foregroundColor(.white)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(canUpload ? Color.blue : Color.gray)
                        .cornerRadius(12)
                    }
                    .disabled(!canUpload)
                }
                
                Spacer()
            }
            .padding()
            .navigationTitle("雲端上傳")
            .navigationBarTitleDisplayMode(.inline)
            .navigationBarBackButtonHidden()
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        dismiss()
                    }
                    .disabled(cloudService.isUploading)
                }
            }
            .alert("上傳錯誤", isPresented: $showingError) {
                Button("確定") { }
            } message: {
                Text(errorMessage)
            }
        }
    }
    
    private var canUpload: Bool {
        return annotationManager.currentAnnotation != nil && !cloudService.isUploading
    }
    
    private func uploadAnnotation() {
        guard let annotationData = annotationManager.exportAnnotationAsCOCO() else {
            errorMessage = "無法導出標註資料"
            showingError = true
            return
        }
        
        Task {
            do {
                // 轉換標註資料格式
                let cloudFormattedData = try cloudService.formatAnnotationDataForCloud(annotationData)
                
                // 建立上傳請求
                let uploadRequest = CloudUploadRequest(
                    annotationData: cloudFormattedData,
                    image: selectedImage,
                    doctorId: doctorId,
                    patientId: patientId,
                    annotationId: UUID().uuidString
                )
                
                // 執行上傳
                let response = try await cloudService.uploadAnnotation(uploadRequest)
                
                await MainActor.run {
                    onUploadComplete(response)
                }
                
            } catch {
                await MainActor.run {
                    if let cloudError = error as? CloudAPIError {
                        errorMessage = cloudError.localizedDescription
                    } else {
                        errorMessage = "上傳失敗：\(error.localizedDescription)"
                    }
                    showingError = true
                }
            }
        }
    }
}