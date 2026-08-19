import SwiftUI

/**
 研究上傳同意（首啟必問；設定頁可改）。

 同意 ↔ 功能的對價要說清楚：同意＝啟用雲端自動辨識，代價是去識別資料上傳；
 不同意＝完全離線手動圈選。兩條路都是完整功能，不是「不同意就不能用」——
 App Store 審查（5.1.1 資料收集與儲存）也要求非必要資料收集不得綁架核心功能。
 */
struct LiteConsentView: View {
    let onChoose: (Bool) -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("選擇您的量測方式").font(.title2).bold()

                    box(title: "🔬 同意研究上傳（啟用自動辨識）", tint: .blue, lines: [
                        "拍攝後由雲端 AI 自動圈出傷口，再由您確認或修改。",
                        "上傳內容：去識別的傷口影像、LiDAR 深度幾何資料（深度圖與相機參數）與量測數值。不含姓名、位置或任何可識別個人的資訊。",
                        "用途：改進傷口辨識與 3D 量測精度的研究與模型訓練。",
                        "可隨時在「設定」撤回；撤回後之後的拍攝不再上傳。",
                    ])

                    box(title: "📴 不同意（完全離線）", tint: .green, lines: [
                        "照片與深度資料全部只留在您的手機（加密儲存）。",
                        "傷口輪廓改由您手動圈選，量測計算全在裝置上完成。",
                        "之後改變心意，隨時可在「設定」開啟研究上傳。",
                    ])

                    Text("兩種方式的量測數學完全相同；差別只在輪廓怎麼來、資料去不去雲端。")
                        .font(.footnote).foregroundStyle(.secondary)

                    Button {
                        onChoose(true)
                    } label: {
                        Text("同意研究上傳，使用自動辨識")
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        onChoose(false)
                    } label: {
                        Text("不同意，完全離線手動圈選")
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.bordered)
                }
                .padding()
            }
            .navigationTitle("WoundLite")
        }
    }

    private func box(title: String, tint: Color, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            ForEach(lines, id: \.self) { l in
                Text("・" + l).font(.subheadline)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.08))
        .cornerRadius(10)
    }
}

/// 設定：研究同意開關＋拍攝要領＋（開發用）後端連線。
struct LiteSettingsView: View {
    @State private var consent = LitePrefs.researchConsent ?? false
    @State private var devURL = AppSettings.backendURL()
    @State private var devUser = AppSettings.backendUser()
    @State private var devPass = ""
    @State private var devMsg: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("研究上傳") {
                    Toggle("上傳去識別資料供研究（啟用自動辨識）", isOn: $consent)
                        .onChange(of: consent) { _, v in LitePrefs.researchConsent = v }
                    Text(consent
                         ? "拍攝的去識別影像與深度幾何會上傳供辨識與精度研究。可隨時關閉。"
                         : "完全離線：照片與深度只留在手機，輪廓手動圈選。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("拍攝要領") {
                    Text("・正對傷口拍攝（斜拍會造成面積誤差，App 會提醒）\n"
                         + "・距離 25–40 公分，等中央對焦框變綠再按快門\n"
                         + "・光線充足、避免反光與濕亮面\n"
                         + "・小於 1 cm² 的傷口誤差較大，數字僅供趨勢參考")
                        .font(.footnote)
                }
                Section("量測結果怎麼讀") {
                    Text("「傷口面積」為皮膚表面實際面積（3D 表面積），拍攝角度改變也不會變。"
                         + "本 App 為健康參考工具，非醫療診斷；傷口有惡化跡象請就醫。")
                        .font(.footnote)
                }
                // 開發驗證用：正式民眾版走匿名 lite 端點（見 docs/lite_backend_contract.md），
                // 帳密欄位只供內部測試對照醫療後端。帳密由使用者自行輸入，App 不預填。
                Section("進階（開發測試）") {
                    TextField("後端網址", text: $devURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("帳號", text: $devUser)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("密碼", text: $devPass)
                    Button("儲存連線設定") {
                        AppSettings.setBackendURL(devURL)
                        if !devUser.isEmpty, !devPass.isEmpty {
                            devMsg = AppSettings.setCredentials(user: devUser, password: devPass)
                                ? "已儲存" : "儲存失敗"
                        } else {
                            devMsg = "已儲存網址（帳密未變更）"
                        }
                    }
                    if let m = devMsg { Text(m).font(.footnote).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("設定")
        }
    }
}
