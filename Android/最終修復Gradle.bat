@echo off
echo ========================================
echo 最終修復 Gradle 問題
echo ========================================
echo.

echo 檢查 Gradle 配置...
if exist "gradle\wrapper\gradle-wrapper.properties" (
    echo ✅ Gradle 配置檔案存在
) else (
    echo ❌ Gradle 配置檔案不存在
    pause
    exit /b 1
)

echo.
echo 清理 Gradle 快取...
if exist "%USERPROFILE%\.gradle\wrapper\dists\gradle-7.6-bin" (
    echo 清理舊的 Gradle 7.6 檔案...
    rmdir /s /q "%USERPROFILE%\.gradle\wrapper\dists\gradle-7.6-bin"
)

if exist "%USERPROFILE%\.gradle\wrapper\dists\gradle-8.4-bin" (
    echo 清理舊的 Gradle 8.4 檔案...
    rmdir /s /q "%USERPROFILE%\.gradle\wrapper\dists\gradle-8.4-bin"
)

echo.
echo 創建 Gradle 目錄...
if not exist "%USERPROFILE%\.gradle" mkdir "%USERPROFILE%\.gradle"
if not exist "%USERPROFILE%\.gradle\wrapper" mkdir "%USERPROFILE%\.gradle\wrapper"
if not exist "%USERPROFILE%\.gradle\wrapper\dists" mkdir "%USERPROFILE%\.gradle\wrapper\dists"

echo.
echo 設定目錄權限...
icacls "%USERPROFILE%\.gradle" /grant "%USERNAME%":(OI)(CI)F /T

echo.
echo ========================================
echo 修復完成！
echo ========================================
echo.
echo 配置已更新：
echo - 使用 Gradle 8.4
echo - 從官方網站下載
echo - 目錄權限已設定
echo.
echo 下一步：
echo 1. 重新開啟 Android Studio
echo 2. 選擇 "File → Invalidate Caches and Restart"
echo 3. 等待專案重新同步
echo 4. 如果仍有問題，檢查網路連線
echo.
pause 