@echo off
echo ========================================
echo 清理 Android 專案過期檔案
echo ========================================
echo.

echo 正在清理測試和過期檔案...

REM 清理測試相關檔案
if exist "快速測試.bat" (
    echo 刪除: 快速測試.bat
    del "快速測試.bat"
)

if exist "測試報告.md" (
    echo 刪除: 測試報告.md
    del "測試報告.md"
)

if exist "測試執行指南.md" (
    echo 刪除: 測試執行指南.md
    del "測試執行指南.md"
)

if exist "Windows測試指南.md" (
    echo 刪除: Windows測試指南.md
    del "Windows測試指南.md"
)

if exist "專案結構測試.ps1" (
    echo 刪除: 專案結構測試.ps1
    del "專案結構測試.ps1"
)

REM 清理修復相關檔案
if exist "修復Gradle權限問題.bat" (
    echo 刪除: 修復Gradle權限問題.bat
    del "修復Gradle權限問題.bat"
)

if exist "修復Gradle權限問題.ps1" (
    echo 刪除: 修復Gradle權限問題.ps1
    del "修復Gradle權限問題.ps1"
)

if exist "修復Gradle同步.bat" (
    echo 刪除: 修復Gradle同步.bat
    del "修復Gradle同步.bat"
)

if exist "修復JDK配置.bat" (
    echo 刪除: 修復JDK配置.bat
    del "修復JDK配置.bat"
)

if exist "快速修復Gradle.bat" (
    echo 刪除: 快速修復Gradle.bat
    del "快速修復Gradle.bat"
)

if exist "快速修復Gradle同步.bat" (
    echo 刪除: 快速修復Gradle同步.bat
    del "快速修復Gradle同步.bat"
)

if exist "設定Gradle環境.bat" (
    echo 刪除: 設定Gradle環境.bat
    del "設定Gradle環境.bat"
)

REM 清理指南和報告檔案
if exist "Android Studio 開啟指南.md" (
    echo 刪除: Android Studio 開啟指南.md
    del "Android Studio 開啟指南.md"
)

if exist "Android Studio 執行指南.md" (
    echo 刪除: Android Studio 執行指南.md
    del "Android Studio 執行指南.md"
)

if exist "Android除錯指南.md" (
    echo 刪除: Android除錯指南.md
    del "Android除錯指南.md"
)

if exist "立即執行指南.md" (
    echo 刪除: 立即執行指南.md
    del "立即執行指南.md"
)

if exist "立即除錯指南.md" (
    echo 刪除: 立即除錯指南.md
    del "立即除錯指南.md"
)

if exist "安裝JDK17指南.md" (
    echo 刪除: 安裝JDK17指南.md
    del "安裝JDK17指南.md"
)

if exist "Gradle同步故障排除.md" (
    echo 刪除: Gradle同步故障排除.md
    del "Gradle同步故障排除.md"
)

if exist "Gradle權限問題解決方案.md" (
    echo 刪除: Gradle權限問題解決方案.md
    del "Gradle權限問題解決方案.md"
)

if exist "專案檢查報告.md" (
    echo 刪除: 專案檢查報告.md
    del "專案檢查報告.md"
)

if exist "建構成功報告.md" (
    echo 刪除: 建構成功報告.md
    del "建構成功報告.md"
)

if exist "修復後測試指南.md" (
    echo 刪除: 修復後測試指南.md
    del "修復後測試指南.md"
)

if exist "最終測試執行指南.md" (
    echo 刪除: 最終測試執行指南.md
    del "最終測試執行指南.md"
)

if exist "Logcat觀察指南.md" (
    echo 刪除: Logcat觀察指南.md
    del "Logcat觀察指南.md"
)

if exist "模擬器相機問題解決指南.md" (
    echo 刪除: 模擬器相機問題解決指南.md
    del "模擬器相機問題解決指南.md"
)

if exist "快速觀察日誌.bat" (
    echo 刪除: 快速觀察日誌.bat
    del "快速觀察日誌.bat"
)

if exist "快速除錯.bat" (
    echo 刪除: 快速除錯.bat
    del "快速除錯.bat"
)

echo.
echo ========================================
echo 清理完成！
echo ========================================
echo.
echo 保留的重要檔案：
echo - 最終測試指南.md (最終測試指南)
echo - 修復完成報告.md (修復總結)
echo - 最終修復Gradle.bat (最終修復腳本)
echo - 醫師標註功能整合說明.md (功能說明)
echo - Android開發架構 (架構文檔)
echo.
echo 已清理的檔案類型：
echo - 測試相關檔案
echo - 修復過程檔案
echo - 過期指南檔案
echo - 重複報告檔案
echo.
pause 