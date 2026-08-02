# iOS/Scripts

這裡放的是**開發／稽核用的獨立腳本**，不參與 App target 編譯。

## ExecuteAnalysisReport.swift

離線產生分析報告用。它的最外層有可執行語句（Swift 只允許 `main.swift` 這樣寫），而且自帶一整組與 `WoundMeasurementApp/Models/` 重複的型別宣告。
留在 App target 內會造成兩類硬性編譯錯誤，因此移到這裡。

執行方式：

```bash
swift iOS/Scripts/ExecuteAnalysisReport.swift
```

## link-opencv.sh

把可選相依 OpenCV 接上或拔掉，見檔內說明。
