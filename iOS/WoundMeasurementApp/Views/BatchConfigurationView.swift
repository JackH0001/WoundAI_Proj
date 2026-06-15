import SwiftUI

struct BatchConfigurationView: View {
    @Environment(\.presentationMode) var presentationMode
    @Binding var config: BatchProcessingConfig
    @State private var tempConfig: BatchProcessingConfig
    
    init(config: Binding<BatchProcessingConfig>) {
        self._config = config
        self._tempConfig = State(initialValue: config.wrappedValue)
    }
    
    var body: some View {
        NavigationView {
            Form {
                // 校正設定
                calibrationSection
                
                // 處理設定
                processingSection
                
                // 性能設定
                performanceSection
                
                // 錯誤處理設定
                errorHandlingSection
            }
            .navigationTitle("批量處理設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("完成") {
                        config = tempConfig
                        presentationMode.wrappedValue.dismiss()
                    }
                    .fontWeight(.semibold)
                }
            }
        }
    }
    
    // MARK: - 校正設定
    private var calibrationSection: some View {
        Section {
            Toggle("啟用校正檢測", isOn: $tempConfig.enableCalibration)
            
            if tempConfig.enableCalibration {
                Toggle("必須校正成功", isOn: $tempConfig.requireCalibration)
                    .disabled(!tempConfig.enableCalibration)
                
                VStack(alignment: .leading, spacing: 8) {
                    Text("預設像素比例")
                        .font(.subheadline)
                    HStack {
                        TextField("像素/毫米", value: $tempConfig.defaultPixelsPerMM, format: .number)
                            .textFieldStyle(RoundedBorderTextFieldStyle())
                        Text("pixels/mm")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
        } header: {
            Text("校正設定")
        } footer: {
            Text("校正檢測用於確定測量的像素比例。如果啟用\"必須校正成功\"，沒有檢測到校正貼紙的圖像將被跳過。")
        }
    }
    
    // MARK: - 處理設定
    private var processingSection: some View {
        Section {
            Toggle("啟用傷口分類", isOn: $tempConfig.enableClassification)
            Toggle("保存到資料庫", isOn: $tempConfig.saveToDatabase)
            
            VStack(alignment: .leading, spacing: 8) {
                Text("處理間隔延遲")
                    .font(.subheadline)
                HStack {
                    Slider(
                        value: $tempConfig.delayBetweenImages,
                        in: 0...2,
                        step: 0.1
                    ) {
                        Text("延遲")
                    } minimumValueLabel: {
                        Text("0s")
                            .font(.caption)
                    } maximumValueLabel: {
                        Text("2s")
                            .font(.caption)
                    }
                    
                    Text("\(tempConfig.delayBetweenImages, specifier: "%.1f")s")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .frame(width: 40)
                }
            }
        } header: {
            Text("處理設定")
        } footer: {
            Text("處理間隔延遲可以減少系統負載，避免過熱和記憶體問題。")
        }
    }
    
    // MARK: - 性能設定
    private var performanceSection: some View {
        Section {
            Toggle("記憶體警告時暫停", isOn: $tempConfig.pauseOnMemoryWarning)
            
            VStack(alignment: .leading, spacing: 8) {
                Text("最大並行處理數")
                    .font(.subheadline)
                Stepper(
                    value: $tempConfig.maxConcurrentProcessing,
                    in: 1...4,
                    step: 1
                ) {
                    HStack {
                        Text("並行數量")
                        Spacer()
                        Text("\(tempConfig.maxConcurrentProcessing)")
                            .foregroundColor(.secondary)
                    }
                }
            }
        } header: {
            Text("性能設定")
        } footer: {
            Text("並行處理目前暫時固定為1。未來版本將支援多線程並行處理以提高效率。")
        }
    }
    
    // MARK: - 錯誤處理設定
    private var errorHandlingSection: some View {
        Section {
            Toggle("遇到錯誤繼續處理", isOn: $tempConfig.continueOnError)
            
            // 預設值重設按鈕
            Button("重設為預設值") {
                tempConfig = BatchProcessingConfig()
            }
            .foregroundColor(.red)
        } header: {
            Text("錯誤處理")
        } footer: {
            Text("如果關閉\"遇到錯誤繼續處理\"，批量處理將在第一個錯誤時停止。")
        }
    }
}

// MARK: - 預覽
#Preview {
    BatchConfigurationView(config: .constant(BatchProcessingConfig()))
}