#!/usr/bin/env bash
# WoundAI iOS — macOS 第一次建置。從 repo 的 iOS/ 目錄執行：bash FIRST_BUILD.sh
#
# 這份程式碼在 Linux 沙箱寫成，**從未經過 Swift 編譯器**。已用靜態稽核清掉
# 缺 import／符號衝突／存取層級／actor 隔離等可離線偵測的問題，但型別推導與
# SwiftUI 的 some View 推斷只有這裡才驗得到。預期仍有殘留錯誤——把輸出貼回即可。
set -o pipefail
cd "$(dirname "$0")"

echo "▶ 0. 環境"
xcodebuild -version || { echo "✗ 找不到 xcodebuild，請先裝 Xcode 並執行 xcode-select --install"; exit 1; }
command -v xcodegen >/dev/null || { echo "▶ 安裝 xcodegen"; brew install xcodegen || exit 1; }

# ⚠ 舊的手工 .xcodeproj 同時收了三個 @main（WoundAIApp / WoundMeasurementApp /
#   ExecuteRealDataAnalysis），而且引用 repo 裡不存在的 opencv2.xcframework 與
#   UNet256.mlmodel。一定要刪掉重新產生，不能直接 open 舊的。
echo "▶ 1. 移除舊的手工專案檔並重新產生"
rm -rf WoundMeasurementApp.xcodeproj
xcodegen generate || exit 1

echo "▶ 2. 建置 App（先只編 App，錯誤比較好讀）"
xcodebuild build \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  2>&1 | tee /tmp/woundai_build.log | grep -E "error:|warning:|BUILD" | head -80

echo
echo "▶ 3. 跑單元測試"
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  CODE_SIGNING_ALLOWED=NO \
  2>&1 | tee /tmp/woundai_test.log | grep -E "error:|Test Case.*(passed|failed)|Executed .* tests|BUILD|TEST" | head -80

echo
echo "════════════════════════════════════════════"
echo "完整日誌：/tmp/woundai_build.log  /tmp/woundai_test.log"
echo "只貼錯誤回來（通常這樣就夠）："
echo "  grep -E 'error:' /tmp/woundai_build.log | sort -u | head -60"
echo "════════════════════════════════════════════"
