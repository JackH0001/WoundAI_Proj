@echo off
echo ========================================
echo 傷口自動化量測 - 簡化打包工具
echo ========================================
echo.

echo 檢查專案狀態...
if exist "app\build.gradle" (
    echo ✅ app\build.gradle 存在
) else (
    echo ❌ app\build.gradle 不存在
    pause
    exit /b 1
)

echo.
echo ========================================
echo 打包選項
echo ========================================
echo.
echo 1. 使用 Android Studio 打包 (推薦)
echo 2. 嘗試命令列打包 (可能失敗)
echo 3. 查看專案狀態
echo 4. 清理專案
echo.
set /p choice="請選擇選項 (1-4): "

if "%choice%"=="1" goto android_studio
if "%choice%"=="2" goto command_line
if "%choice%"=="3" goto check_status
if "%choice%"=="4" goto clean_project
goto invalid_choice

:android_studio
echo.
echo ========================================
echo 使用 Android Studio 打包
echo ========================================
echo.
echo 請按照以下步驟操作：
echo.
echo 1. 開啟 Android Studio
echo 2. 選擇 "Open an existing project"
echo 3. 導航到：%CD%
echo 4. 等待專案同步完成
echo 5. 選擇 Build → Build Bundle(s) / APK(s) → Build APK(s)
echo 6. 等待建構完成
echo 7. 點擊 "locate" 找到 APK 檔案
echo.
echo APK 檔案位置通常在：
echo app\build\outputs\apk\debug\app-debug.apk
echo.
echo 注意：Android Studio 會自動處理 Java 版本問題
echo.
pause
goto end

:command_line
echo.
echo ========================================
echo 嘗試命令列打包
echo ========================================
echo.
echo 警告：由於 Java 版本兼容性問題，此方法可能失敗
echo 建議使用 Android Studio 進行打包
echo.
set /p confirm="確定要繼續嗎？(y/n): "
if /i not "%confirm%"=="y" goto end

echo.
echo 執行 Gradle 打包...
call gradlew.bat assembleDebug

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ 打包成功！
    echo.
    echo APK 檔案位置：
    echo app\build\outputs\apk\debug\app-debug.apk
    echo.
    if exist "app\build\outputs\apk\debug\app-debug.apk" (
        echo 檔案大小：
        dir "app\build\outputs\apk\debug\app-debug.apk"
    )
) else (
    echo.
    echo ❌ 打包失敗！
    echo.
    echo 建議解決方案：
    echo 1. 使用 Android Studio 打包
    echo 2. 安裝 JDK 17 並設定 JAVA_HOME
    echo 3. 參考 "最終打包解決方案.md"
)

pause
goto end

:check_status
echo.
echo ========================================
echo 專案狀態檢查
echo ========================================
echo.
echo 檢查核心檔案...
if exist "app\build.gradle" echo ✅ app\build.gradle
if exist "gradle\wrapper\gradle-wrapper.jar" echo ✅ gradle-wrapper.jar
if exist "gradle\wrapper\gradle-wrapper.properties" echo ✅ gradle-wrapper.properties
if exist "app\src\main\AndroidManifest.xml" echo ✅ AndroidManifest.xml
if exist "app\src\main\java\com\woundmeasurement\app\MainActivity.kt" echo ✅ MainActivity.kt

echo.
echo 檢查 Gradle 版本...
call gradlew.bat --version | findstr "Gradle"

echo.
echo 檢查 Java 版本...
java -version

echo.
echo 檢查 APK 檔案...
if exist "app\build\outputs\apk\debug\app-debug.apk" (
    echo ✅ APK 檔案已存在
    dir "app\build\outputs\apk\debug\app-debug.apk"
) else (
    echo ❌ APK 檔案不存在
)

pause
goto end

:clean_project
echo.
echo ========================================
echo 清理專案
echo ========================================
echo.
echo 清理建構檔案...
call gradlew.bat clean

if %ERRORLEVEL% EQU 0 (
    echo ✅ 清理成功！
) else (
    echo ❌ 清理失敗！
)

pause
goto end

:invalid_choice
echo.
echo ❌ 無效的選項，請重新選擇
echo.
pause
goto end

:end
echo.
echo ========================================
echo 操作完成
echo ========================================
echo. 