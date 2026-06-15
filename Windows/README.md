# 傷口自動化量測系統 - Windows 平台

## 專案概述

這是一個基於 Windows 平台的模組化傷口自動化量測系統，採用 C# WPF 技術開發，整合了 OpenCV、ONNX Runtime 等先進技術，提供完整的傷口影像捕捉、處理、AI 分析和量測功能。

## 系統架構

### 模組化設計
系統採用高度模組化的設計，包含以下核心模組：

1. **Core 模組** - 核心介面和基礎類別
2. **Capture 模組** - 影像捕捉功能
3. **Processing 模組** - 影像前處理和品質評估
4. **AI 模組** - 機器學習推論和分類
5. **Measurement 模組** - 傷口量測計算
6. **Data 模組** - 資料儲存和管理
7. **WPF 模組** - 使用者介面
8. **Tests 模組** - 單元測試

### 技術棧
- **.NET 8.0** - 主要開發框架
- **WPF** - 使用者介面框架
- **Material Design** - UI 設計語言
- **OpenCV 4** - 電腦視覺處理
- **ONNX Runtime** - 機器學習推論
- **Entity Framework Core** - 資料存取
- **SQLite** - 本地資料庫
- **Dependency Injection** - 依賴注入
- **MVVM Pattern** - 架構模式

## 功能特色

### 影像捕捉與載入
- 支援多種攝影機設備（USB、內建、ToF 深度攝影機）
- 即時影像預覽
- 可調整解析度和幀率
- 自動曝光和白平衡控制
- **本地影像載入**: 支援載入 JPG、PNG、BMP、TIFF 等格式的本地影像檔案
- **影像品質評估**: 自動評估載入影像的品質和適用性

### 影像處理
- 白平衡校正
- Gamma 校正
- 雜訊抑制
- 邊緣增強
- 品質評估（SNR、深度覆蓋率、運動模糊檢測）

### AI 分析
- 傷口類型分類（急性/慢性）
- 傷口區域分割
- 癒合進度預測
- 風險評估
- 治療建議

### 量測功能
- 傷口面積計算
- 周長測量
- 深度分析（需深度攝影機）
- 體積計算
- 邊界提取

### 資料管理
- 本地 SQLite 資料庫
- 量測歷史記錄
- 影像儲存
- 報告生成

## 系統需求

### 硬體需求
- **處理器**: Intel i5 或 AMD Ryzen 5 以上
- **記憶體**: 8GB RAM 以上
- **儲存空間**: 2GB 可用空間
- **攝影機**: USB 網路攝影機或內建攝影機
- **顯示器**: 1920x1080 以上解析度

### 軟體需求
- **作業系統**: Windows 10/11 (64-bit)
- **.NET Runtime**: .NET 8.0 Desktop Runtime
- **Visual Studio**: 2022 或更新版本（開發用）

## 安裝與部署

### 開發環境設定
1. 安裝 Visual Studio 2022
2. 安裝 .NET 8.0 SDK
3. 複製專案到本地
4. 開啟 `WoundMeasurementSystem.sln`
5. 還原 NuGet 套件
6. 建置解決方案

### 執行應用程式
```bash
# 建置專案
dotnet build

# 執行應用程式
dotnet run --project WPF/WoundMeasurement.WPF.csproj
```

### 部署
```bash
# 發布應用程式
dotnet publish WPF/WoundMeasurement.WPF.csproj -c Release -r win-x64 --self-contained

# 建立安裝程式（需要額外工具）
```

## 使用指南

### 基本操作流程
1. **啟動應用程式** - 系統會自動初始化各模組
2. **選擇影像來源**:
   - **即時捕捉**: 點擊「開始捕捉」按鈕啟動攝影機
   - **載入本地影像**: 點擊「載入本地影像」按鈕選擇檔案
3. **調整設定** - 根據需要調整解析度、品質等參數
4. **執行量測** - 點擊「單次量測」或「處理載入影像」
5. **查看結果** - 在右側面板查看量測結果和影像
6. **儲存資料** - 系統會自動儲存量測記錄

### 進階功能
- **校準功能** - 使用參考物件進行像素校準
- **本地影像處理** - 支援載入和分析本地影像檔案
- **批次處理** - 支援多張影像批次處理
- **報告匯出** - 匯出 PDF 或 Excel 報告
- **資料備份** - 備份和還原量測資料

## 開發指南

### 專案結構
```
Windows/
├── Core/                    # 核心模組
│   ├── Models/             # 資料模型
│   ├── Interfaces/         # 介面定義
│   └── Services/           # 核心服務
├── Capture/                # 捕捉模組
│   └── Modules/           # 捕捉實作
├── Processing/             # 處理模組
├── AI/                     # AI 模組
├── Measurement/            # 量測模組
├── Data/                   # 資料模組
├── WPF/                    # WPF 應用程式
│   ├── Views/             # 視窗和頁面
│   ├── ViewModels/        # ViewModel 類別
│   ├── Styles/            # 樣式資源
│   └── Converters/        # 值轉換器
└── Tests/                  # 測試專案
```

### 擴展開發
1. **新增捕捉模組** - 實作 `ICaptureModule` 介面
2. **新增處理演算法** - 擴展 `IProcessingModule`
3. **整合新 AI 模型** - 實作 `IAIModule` 介面
4. **自定義量測方法** - 擴展 `IMeasurementModule`

### 測試
```bash
# 執行所有測試
dotnet test

# 執行特定測試專案
dotnet test Tests/WoundMeasurement.Tests.csproj

# 生成測試覆蓋率報告
dotnet test --collect:"XPlat Code Coverage"
```

## 配置檔案

### appsettings.json
```json
{
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft": "Warning"
    }
  },
  "Capture": {
    "DefaultResolution": "640x480",
    "DefaultFrameRate": 30,
    "EnableDepthCapture": false
  },
  "Processing": {
    "EnableWhiteBalance": true,
    "EnableNoiseReduction": true,
    "MinQualityScore": 20.0
  },
  "AI": {
    "ModelPath": "Models/wound_classification.onnx",
    "UseGPU": false,
    "MinConfidenceThreshold": 0.5
  },
  "Measurement": {
    "PixelSizeMm": 0.1,
    "EnableDepthMeasurement": true
  }
}
```

## 故障排除

### 常見問題
1. **攝影機無法開啟** - 檢查攝影機權限和驅動程式
2. **AI 模型載入失敗** - 確認模型檔案路徑和格式
3. **量測結果不準確** - 執行像素校準
4. **應用程式崩潰** - 檢查日誌檔案

### 日誌檔案
應用程式日誌位於：
- `%APPDATA%\WoundMeasurement\Logs\`
- 或專案目錄下的 `Logs\` 資料夾

## 授權與版權

本專案採用 MIT 授權條款，詳見 LICENSE 檔案。

## 貢獻指南

歡迎提交 Issue 和 Pull Request 來改善專案。

## 聯絡資訊

如有問題或建議，請透過以下方式聯絡：
- 專案 Issues: GitHub Issues
- 電子郵件: [您的郵箱]

---

**版本**: 1.0.0  
**更新日期**: 2024年12月  
**開發團隊**: [您的團隊名稱] 