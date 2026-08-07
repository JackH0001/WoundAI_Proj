import SwiftUI

struct CloudAuthenticationView: View {
    @Binding var doctorId: String
    @Binding var password: String
    @Binding var patientId: String
    @ObservedObject var cloudService: CloudAPIService
    let onAuthenticated: () -> Void
    
    @State private var isAuthenticating = false
    @State private var errorMessage = ""
    @State private var showingError = false
    @Environment(\.dismiss) private var dismiss
    
    var body: some View {
        NavigationView {
            Form {
                Section("醫師認證") {
                    TextField("醫師ID", text: $doctorId)
                        .textContentType(.username)
                        .autocapitalization(.none)
                    
                    SecureField("密碼", text: $password)
                        .textContentType(.password)
                }
                
                Section("病患資訊（選填）") {
                    TextField("病患ID", text: $patientId)
                        .textContentType(.none)
                        .autocapitalization(.none)
                }
                
                Section {
                    Button(action: authenticateUser) {
                        HStack {
                            if isAuthenticating {
                                ProgressView()
                                    .scaleEffect(0.8)
                                    .padding(.trailing, 5)
                            }
                            Text(isAuthenticating ? "認證中..." : "登入雲端平台")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .disabled(doctorId.isEmpty || password.isEmpty || isAuthenticating)
                }
                
                Section("說明") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("• 請使用您的醫師帳號登入雲端AI模型訓練平台")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        
                        Text("• 標註資料將安全上傳至雲端進行AI模型訓練")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        
                        Text("• 病患ID為選填項目，用於資料追蹤管理")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .navigationTitle("雲端平台認證")
            .navigationBarTitleDisplayMode(.inline)
            .navigationBarBackButtonHidden()
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("取消") {
                        dismiss()
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    if isAuthenticating {
                        ProgressView()
                            .scaleEffect(0.8)
                    }
                }
            }
            .alert("認證錯誤", isPresented: $showingError) {
                Button("確定") { }
            } message: {
                Text(errorMessage)
            }
        }
    }
    
    private func authenticateUser() {
        guard !doctorId.isEmpty && !password.isEmpty else { return }
        
        isAuthenticating = true
        errorMessage = ""
        
        Task {
            do {
                try await cloudService.authenticate(doctorId: doctorId, password: password)
                
                await MainActor.run {
                    isAuthenticating = false
                    onAuthenticated()
                }
                
            } catch {
                await MainActor.run {
                    isAuthenticating = false
                    
                    if let cloudError = error as? CloudAPIError {
                        errorMessage = cloudError.localizedDescription
                    } else {
                        errorMessage = "認證失敗：\(error.localizedDescription)"
                    }
                    
                    showingError = true
                }
            }
        }
    }
}