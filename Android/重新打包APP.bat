@echo off
echo ========================================
echo 重新打包 Android 應用程式
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

if exist "gradle\wrapper\gradle-wrapper.jar" (
    echo ✅ gradle-wrapper.jar 存在
) else (
    echo ❌ gradle-wrapper.jar 不存在
    pause
    exit /b 1
)

echo.
echo 清理舊的建構檔案...
if exist "app\build" (
    echo 刪除 app\build 目錄
    rmdir /s /q "app\build"
)

if exist "build" (
    echo 刪除 build 目錄
    rmdir /s /q "build"
)

echo.
echo ========================================
echo 打包選項
echo ========================================
echo.
echo 1. 使用 Android Studio 打包 (推薦)
echo 2. 使用 Gradle 命令列打包
echo 3. 只清理專案
echo.
set /p choice="請選擇選項 (1-3): "

if "%choice%"=="1" goto android_studio
if "%choice%"=="2" goto gradle_cli
if "%choice%"=="3" goto clean_only
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
pause
goto end

:gradle_cli
echo.
echo ========================================
echo 使用 Gradle 命令列打包
echo ========================================
echo.
echo 注意：由於 Java 版本兼容性問題，建議使用 Android Studio
echo.
echo 嘗試使用 Gradle 打包...
echo.

REM 設定 JAVA_HOME 為較舊的版本（如果有的話）
if exist "C:\Program Files\Java\jdk-17" (
    set "JAVA_HOME=C:\Program Files\Java\jdk-17"
    echo 設定 JAVA_HOME 為 JDK 17
) else if exist "C:\Program Files\Java\jdk-11" (
    set "JAVA_HOME=C:\Program Files\Java\jdk-11"
    echo 設定 JAVA_HOME 為 JDK 11
) else (
    echo 警告：未找到合適的 JDK 版本
    echo 建議安裝 JDK 17 或 JDK 11
)

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
    echo 建議使用 Android Studio 進行打包
)

pause
goto end

:clean_only
echo.
echo ========================================
echo 清理專案
echo ========================================
echo.
echo 清理完成！
echo 專案已準備好重新建構
echo.
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