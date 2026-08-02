#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# link-opencv.sh — 把「可選相依」的 OpenCV 接上（或拔掉）
#
# 用法：
#   iOS/Scripts/link-opencv.sh            # 偵測 xcframework，有就啟用、沒有就停用
#   iOS/Scripts/link-opencv.sh --disable  # 強制停用（刪除 local xcconfig）
#   iOS/Scripts/link-opencv.sh --check    # 只回報狀態，不改檔（exit 0=已啟用, 1=停用）
#
# 原理：產生 / 移除 iOS/Config/OpenCV.local.xcconfig，
#       該檔由 iOS/Config/OpenCV.xcconfig 以 `#include?` 可選引入。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

IOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XCF="${IOS_DIR}/WoundMeasurementApp/opencv2.xcframework"
LOCAL="${IOS_DIR}/Config/OpenCV.local.xcconfig"

MODE="${1:-auto}"

if [[ "$MODE" == "--disable" ]]; then
  rm -f "$LOCAL"
  echo "OpenCV: 已停用（stub 模式）。已移除 $(basename "$LOCAL")"
  exit 0
fi

if [[ "$MODE" == "--check" ]]; then
  if [[ -f "$LOCAL" ]]; then echo "OpenCV: 已啟用"; exit 0; else echo "OpenCV: 停用（stub 模式）"; exit 1; fi
fi

if [[ ! -d "$XCF" ]]; then
  rm -f "$LOCAL"
  cat <<EOF
OpenCV: 找不到 ${XCF}
        → 維持 stub 模式（可正常編譯，圓形偵測走 Core Image Hough 備援）。
        要啟用請先取得 xcframework：
          • GitHub Actions → "Build OpenCV xcframework (one-time)" → Run workflow
          • 或本機自建（見 .github/workflows/build-opencv-xcframework.yml 註解）
        放到 iOS/WoundMeasurementApp/ 之下後重跑本腳本。
EOF
  exit 0
fi

# 驗證兩個必要 slice 都在（與 ios.yml 的 Validate 步驟一致）
missing=0
for slice in ios-arm64 ios-arm64_x86_64-simulator; do
  if [[ ! -d "${XCF}/${slice}/opencv2.framework/Headers" ]]; then
    echo "✗ 缺少 slice: ${slice}" >&2; missing=1
  fi
done
if [[ $missing -ne 0 ]]; then
  echo "現有 slices：" >&2; ls -1 "$XCF" >&2
  echo "xcframework 不完整，請以 --iphoneos_archs arm64 --iphonesimulator_archs arm64,x86_64 重建。" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOCAL")"
cat > "$LOCAL" <<'EOF'
// 自動產生 — 請勿手改，也不要提交（見 .gitignore）。
// 由 iOS/Scripts/link-opencv.sh 產生；停用請執行 link-opencv.sh --disable
OPENCV_XCFRAMEWORK = $(SRCROOT)/WoundMeasurementApp/opencv2.xcframework

// 依 SDK 選擇正確的 slice（避免把裝置與模擬器兩個架構同時餵給 linker）
OPENCV_SLICE[sdk=iphoneos*] = $(OPENCV_XCFRAMEWORK)/ios-arm64
OPENCV_SLICE[sdk=iphonesimulator*] = $(OPENCV_XCFRAMEWORK)/ios-arm64_x86_64-simulator

OPENCV_FRAMEWORK_SEARCH_PATHS = "$(OPENCV_SLICE)"
OPENCV_HEADER_SEARCH_PATHS = "$(OPENCV_SLICE)/opencv2.framework/Headers"
OPENCV_LDFLAGS = -framework opencv2
EOF

echo "OpenCV: 已啟用 → $LOCAL"
echo "  slices: $(ls -1 "$XCF" | tr '\n' ' ')"
