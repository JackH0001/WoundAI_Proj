# 標尺刻度辨識與校正系統設計

## 系統概覽

基於三平台現有架構，設計統一的標尺校正系統，以解決當前測量精度不足的問題。

---

## 📏 標尺設計規範

### 物理規格
- **尺寸**: 30mm × 30mm 正方形網格
- **刻度精度**: 1mm 標準間距
- **材質**: 醫療級矽膠，可高溫滅菌
- **顏色方案**: 
  - 主標線：黑色 (每5mm)
  - 次標線：灰色 (每1mm)  
  - 背景：白色無反光
  - 角落標記：彩色編碼點 (紅藍綠黃)

### 定位標記
```
┌─R─────────┬─────────┬─────────B─┐
│           │         │           │
├───────────┼─────────┼───────────┤
│           │         │           │
├───────────┼─────────┼───────────┤
│           │         │           │  
└─Y─────────┴─────────┴─────────G─┘
R=紅色 B=藍色 Y=黃色 G=綠色
```

---

## 🔍 檢測演算法架構

### 第一階段：標尺檢測
```python
def detect_ruler(image):
    # 1. 預處理
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # 2. 邊緣檢測
    edges = cv2.Canny(blurred, 50, 150)
    
    # 3. 線段檢測
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 
                           minLineLength=20, maxLineGap=5)
    
    # 4. 網格模式識別
    grid_pattern = identify_grid_pattern(lines)
    
    # 5. 彩色角點檢測
    corner_points = detect_color_corners(image)
    
    return grid_pattern, corner_points
```

### 第二階段：透視校正
```python
def perspective_correction(image, corner_points):
    # 1. 四角點排序
    ordered_corners = order_corner_points(corner_points)
    
    # 2. 計算透視變換矩陣
    target_corners = np.array([[0,0], [30,0], [30,30], [0,30]])
    transform_matrix = cv2.getPerspectiveTransform(
        ordered_corners, target_corners)
    
    # 3. 應用透視校正
    corrected = cv2.warpPerspective(
        image, transform_matrix, (300, 300))  # 300px = 30mm
    
    return corrected, transform_matrix
```

### 第三階段：像素比例計算
```python
def calculate_pixel_scale(corrected_ruler):
    # 1. 檢測校正後的格線
    horizontal_lines = detect_horizontal_grid(corrected_ruler)
    vertical_lines = detect_vertical_grid(corrected_ruler)
    
    # 2. 計算像素間距
    h_spacing = np.mean(np.diff(horizontal_lines))
    v_spacing = np.mean(np.diff(vertical_lines))
    
    # 3. 計算比例 (1mm = ? pixels)
    pixel_per_mm = (h_spacing + v_spacing) / 2.0
    
    return pixel_per_mm
```

---

## 🎯 平台整合方案

### iOS平台整合 (Swift)
```swift
// 整合到 SmartROIModule.swift
class RulerCalibrationModule {
    func detectAndCalibrateRuler(image: UIImage) -> CalibrationResult? {
        guard let cgImage = image.cgImage else { return nil }
        
        // 轉換為OpenCV格式
        let cvImage = convertToCVMat(cgImage)
        
        // 檢測標尺
        let (gridPattern, corners) = detectRuler(cvImage)
        
        // 透視校正
        let (corrected, transform) = perspectiveCorrection(cvImage, corners)
        
        // 計算比例
        let pixelScale = calculatePixelScale(corrected)
        
        return CalibrationResult(
            pixelPerMM: pixelScale,
            transformMatrix: transform,
            confidence: calculateConfidence(gridPattern, corners)
        )
    }
}
```

### Windows平台整合 (C#)
```csharp
// 新增到 Windows/Capture/Modules/
public class RulerCalibrationModule
{
    public CalibrationResult DetectAndCalibrateRuler(Mat image)
    {
        // 1. 標尺檢測
        var (gridPattern, corners) = DetectRuler(image);
        
        // 2. 透視校正
        var (corrected, transform) = PerspectiveCorrection(image, corners);
        
        // 3. 像素比例計算
        var pixelScale = CalculatePixelScale(corrected);
        
        return new CalibrationResult
        {
            PixelPerMM = pixelScale,
            TransformMatrix = transform,
            Confidence = CalculateConfidence(gridPattern, corners)
        };
    }
}
```

### Android平台整合 (Kotlin)
```kotlin
// 新增到 Android/app/src/main/java/com/woundmeasurement/app/calibration/
class RulerCalibrationModule {
    fun detectAndCalibrateRuler(bitmap: Bitmap): CalibrationResult? {
        val mat = Mat()
        Utils.bitmapToMat(bitmap, mat)
        
        // 標尺檢測
        val (gridPattern, corners) = detectRuler(mat)
        
        // 透視校正
        val (corrected, transform) = perspectiveCorrection(mat, corners)
        
        // 像素比例計算
        val pixelScale = calculatePixelScale(corrected)
        
        return CalibrationResult(
            pixelPerMM = pixelScale,
            transformMatrix = transform,
            confidence = calculateConfidence(gridPattern, corners)
        )
    }
}
```

---

## 📊 精度改善預期

### 校正前後比較
| 平台 | 校正前精度 | 校正後精度 | 改善幅度 |
|------|------------|------------|----------|
| iOS | ±15-20% | ±3-5% | **+12-17%** |
| Windows | ±10-15% | ±2-4% | **+8-13%** |
| Android | ±20-30% | ±3-6% | **+17-27%** |

### 使用場景建議
1. **必須使用標尺**：
   - Android平台 (無深度感測器)
   - 臨床研究 (≥98%精度要求)
   - 法醫鑑定 (法律證據級別)

2. **建議使用標尺**：
   - iOS平面拍攝模式
   - Windows非RealSense模式
   - 首次校正或定期驗證

3. **可選用標尺**：
   - iOS ARKit深度模式 (驗證用)
   - Windows RealSense模式 (驗證用)

---

## 🔄 工作流程整合

### 使用者操作流程
1. **準備階段**：在傷口旁邊放置標尺
2. **拍攝階段**：確保標尺完全可見且清晰
3. **檢測階段**：系統自動檢測標尺並校正
4. **確認階段**：顯示校正結果，使用者確認
5. **測量階段**：使用校正參數進行精確測量

### API整合端點
```json
POST /api/measurement/calibrate
{
  "image": "base64_encoded_image",
  "platform": "ios|android|windows",
  "use_ruler": true
}

Response:
{
  "calibration_result": {
    "pixel_per_mm": 10.5,
    "confidence": 0.95,
    "ruler_detected": true
  },
  "measurement_result": {
    "area_cm2": 2.34,
    "perimeter_cm": 6.78,
    "accuracy_estimate": "±3%"
  }
}
```

---

## 🎯 第二階段實作計畫

將標尺校正系統納入 **Gartner專案排程第二階段 (週5-8)**：

### 里程碑 2.1 擴展：統一校正標準
- [ ] 標尺校正模組開發 (iOS/Android/Windows)
- [ ] 透視校正演算法實作
- [ ] 彩色角點檢測系統
- [ ] 跨平台校正API統一

### 驗收標準更新
- 校正後測量精度：iOS ≥95%, Windows ≥96%, Android ≥94%
- 標尺檢測成功率 ≥98%
- 校正處理時間 ≤500ms

---

**預期效果**：實施標尺校正系統後，所有平台測量精度可達醫療級標準（±5%以內），滿足臨床應用需求。