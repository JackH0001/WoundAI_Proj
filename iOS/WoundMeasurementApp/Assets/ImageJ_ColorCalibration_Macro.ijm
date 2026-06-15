/*
 * ImageJ色彩校正Macro腳本 v2.0
 * 適用於方形RGBY校正貼紙的自動化色彩校正
 * 支援透視校正和色彩矩陣計算
 */

// 全域變數定義
var standardColors = newArray(4);
standardColors[0] = "255,0,0";     // 紅色 (R)
standardColors[1] = "255,255,0";   // 黃色 (Y) 
standardColors[2] = "0,255,0";     // 綠色 (G)
standardColors[3] = "0,0,255";     // 藍色 (B)

var colorNames = newArray("Red", "Yellow", "Green", "Blue");
var grayValue = "45,45,45"; // 18%灰 #2D2D2D

// 主函數：自動色彩校正
macro "Auto Color Calibration" {
    // 檢查是否有開啟的圖像
    if (nImages == 0) {
        showMessage("錯誤", "請先開啟包含校正貼紙的圖像");
        exit();
    }
    
    originalTitle = getTitle();
    originalID = getImageID();
    
    print("開始自動色彩校正...");
    print("圖像: " + originalTitle);
    
    // Step 1: 檢測方形校正貼紙
    if (!detectSquareSticker()) {
        showMessage("錯誤", "未檢測到方形校正貼紙");
        exit();
    }
    
    // Step 2: 檢測四角凸點並進行透視校正  
    if (!detectAndCorrectPerspective()) {
        showMessage("警告", "透視校正失敗，繼續進行色彩校正");
    }
    
    // Step 3: 檢測RGBY色彩點
    colorPoints = detectColorPoints();
    if (colorPoints.length < 3) {
        showMessage("錯誤", "檢測到的色彩點不足(<3個)");
        exit();
    }
    
    // Step 4: 計算色彩校正矩陣
    colorMatrix = calculateColorMatrix(colorPoints);
    
    // Step 5: 應用色彩校正
    applyColorCorrection(colorMatrix);
    
    // Step 6: 驗證校正效果
    validateCorrection();
    
    print("色彩校正完成!");
    showResults();
}

// 檢測方形校正貼紙
function detectSquareSticker() {
    selectImage(originalID);
    
    // 轉為8位灰階
    run("Duplicate...", "title=Gray_Detection");
    run("8-bit");
    
    // 邊緣檢測
    run("Gaussian Blur...", "sigma=1.0");
    run("Find Edges");
    
    // 尋找方形輪廓
    setAutoThreshold("Default");
    run("Convert to Mask");
    
    // 分析粒子找出方形
    run("Analyze Particles...", 
        "size=1000-50000 " +  // 調整尺寸範圍
        "circularity=0.0-0.2 " + // 低圓形度（方形）
        "show=Overlay " +
        "display clear");
    
    // 檢查是否找到合適的方形
    if (nResults == 0) {
        close("Gray_Detection");
        return false;
    }
    
    // 選擇最大的方形區域
    largestArea = 0;
    bestIndex = 0;
    for (i = 0; i < nResults; i++) {
        area = getResult("Area", i);
        if (area > largestArea) {
            largestArea = area;
            bestIndex = i;
        }
    }
    
    // 獲取方形邊界框
    x = getResult("BX", bestIndex);
    y = getResult("BY", bestIndex);
    width = getResult("Width", bestIndex);
    height = getResult("Height", bestIndex);
    
    // 儲存方形區域資訊
    call("ij.Prefs.set", "square.x", x);
    call("ij.Prefs.set", "square.y", y);
    call("ij.Prefs.set", "square.width", width);
    call("ij.Prefs.set", "square.height", height);
    
    close("Gray_Detection");
    
    print("檢測到方形校正貼紙:");
    print("  位置: (" + x + ", " + y + ")");
    print("  尺寸: " + width + " x " + height);
    print("  面積: " + largestArea + " 像素");
    
    return true;
}

// 檢測四角凸點並進行透視校正
function detectAndCorrectPerspective() {
    selectImage(originalID);
    
    // 獲取方形區域
    x = call("ij.Prefs.get", "square.x", 0);
    y = call("ij.Prefs.get", "square.y", 0);
    width = call("ij.Prefs.get", "square.width", 100);
    height = call("ij.Prefs.get", "square.height", 100);
    
    // 裁切方形區域
    makeRectangle(x, y, width, height);
    run("Duplicate...", "title=Square_Region");
    run("Select None");
    
    // 檢測角點（使用Harris角點檢測）
    run("32-bit");
    run("FeatureJ Harris", "compute smoothing=1.0 integration=1.0 " + 
        "eigenvalue=small suppress minimum");
    
    // 找出四個最強的角點
    run("Find Maxima...", "prominence=0.1 output=[Point Selection]");
    
    if (selectionType() != 10) { // 10 = point selection
        close("Square_Region");
        return false;
    }
    
    // 獲取選擇的點
    getSelectionCoordinates(xpoints, ypoints);
    
    if (xpoints.length < 4) {
        close("Square_Region");
        return false;
    }
    
    // 排序角點（左上、右上、右下、左下）
    cornerPoints = sortCornerPoints(xpoints, ypoints, width, height);
    
    // 應用透視變換
    selectImage(originalID);
    
    // 設置源點和目標點
    srcPoints = "" + (cornerPoints[0] + x) + "," + (cornerPoints[1] + y) + " " +
               "" + (cornerPoints[2] + x) + "," + (cornerPoints[3] + y) + " " +
               "" + (cornerPoints[4] + x) + "," + (cornerPoints[5] + y) + " " +
               "" + (cornerPoints[6] + x) + "," + (cornerPoints[7] + y);
    
    targetSize = 400; // 目標尺寸（像素）
    dstPoints = "0,0 " + targetSize + ",0 " + targetSize + "," + targetSize + " 0," + targetSize;
    
    // 執行透視變換
    run("Perspective Transform", 
        "source=[" + srcPoints + "] " +
        "target=[" + dstPoints + "] " +
        "size=" + targetSize);
    
    close("Square_Region");
    
    print("透視校正完成");
    print("  角點: " + srcPoints);
    print("  目標尺寸: " + targetSize + "x" + targetSize);
    
    return true;
}

// 排序角點
function sortCornerPoints(x, y, w, h) {
    // 簡化版本：按座標排序
    points = newArray(8);
    
    // 找最接近四角的點
    corners = newArray(4);
    corners[0] = findClosestPoint(x, y, 0, 0);         // 左上
    corners[1] = findClosestPoint(x, y, w, 0);         // 右上
    corners[2] = findClosestPoint(x, y, w, h);         // 右下
    corners[3] = findClosestPoint(x, y, 0, h);         // 左下
    
    for (i = 0; i < 4; i++) {
        idx = corners[i];
        points[i*2] = x[idx];
        points[i*2+1] = y[idx];
    }
    
    return points;
}

// 找最接近目標位置的點
function findClosestPoint(x, y, targetX, targetY) {
    minDist = 999999;
    closestIdx = 0;
    
    for (i = 0; i < x.length; i++) {
        dist = sqrt((x[i] - targetX) * (x[i] - targetX) + 
                   (y[i] - targetY) * (y[i] - targetY));
        if (dist < minDist) {
            minDist = dist;
            closestIdx = i;
        }
    }
    
    return closestIdx;
}

// 檢測RGBY色彩點
function detectColorPoints() {
    currentImage = getImageID();
    
    colorPoints = newArray(0);
    
    // 為每種顏色創建遮罩並找中心點
    for (i = 0; i < colorNames.length; i++) {
        selectImage(currentImage);
        
        colorName = colorNames[i];
        print("檢測 " + colorName + " 色彩點...");
        
        // 創建HSV遮罩
        run("Duplicate...", "title=" + colorName + "_Detection");
        run("HSV Stack");
        
        // 根據顏色設定HSV閾值
        hsvThresholds = getHSVThresholds(colorName);
        
        // 應用顏色範圍選擇
        setSlice(1); // H通道
        setThreshold(hsvThresholds[0], hsvThresholds[1]);
        run("Convert to Mask");
        
        setSlice(2); // S通道  
        setThreshold(hsvThresholds[2], hsvThresholds[3]);
        run("Convert to Mask");
        
        setSlice(3); // V通道
        setThreshold(hsvThresholds[4], hsvThresholds[5]);
        run("Convert to Mask");
        
        // 合併遮罩
        run("Z Project...", "projection=[Min Intensity]");
        
        // 找最大連通區域
        run("Analyze Particles...", 
            "size=50-2000 circularity=0.3-1.0 " +
            "show=Nothing display clear");
        
        if (nResults > 0) {
            // 選擇最大區域
            maxArea = 0;
            bestIdx = 0;
            for (j = 0; j < nResults; j++) {
                area = getResult("Area", j);
                if (area > maxArea) {
                    maxArea = area;
                    bestIdx = j;
                }
            }
            
            // 獲取中心座標
            centerX = getResult("XM", bestIdx);
            centerY = getResult("YM", bestIdx);
            
            // 獲取實際顏色值
            selectImage(currentImage);
            actualColor = getPixelColor(centerX, centerY);
            
            // 儲存色彩點資訊
            pointInfo = colorName + ":" + centerX + "," + centerY + ":" + actualColor;
            colorPoints = Array.concat(colorPoints, pointInfo);
            
            print("  找到 " + colorName + " 於 (" + centerX + ", " + centerY + ")");
            print("  實際色彩: " + actualColor);
        } else {
            print("  未找到 " + colorName + " 色彩點");
        }
        
        // 清理臨時圖像
        selectWindow(colorName + "_Detection");
        close();
        selectWindow("MIN_" + colorName + "_Detection");
        close();
    }
    
    return colorPoints;
}

// 獲取HSV閾值範圍
function getHSVThresholds(colorName) {
    if (colorName == "Red") {
        return newArray(0, 10, 100, 255, 100, 255);  // 紅色
    } else if (colorName == "Yellow") {
        return newArray(20, 30, 100, 255, 100, 255); // 黃色
    } else if (colorName == "Green") {
        return newArray(40, 80, 100, 255, 100, 255); // 綠色
    } else if (colorName == "Blue") {
        return newArray(100, 130, 100, 255, 100, 255); // 藍色
    } else {
        return newArray(0, 180, 0, 30, 40, 80); // 灰色（低飽和度）
    }
}

// 獲取像素色彩
function getPixelColor(x, y) {
    r = getPixel(x, y) & 0xff0000; r = r >> 16;
    g = getPixel(x, y) & 0xff00; g = g >> 8;
    b = getPixel(x, y) & 0xff;
    return "" + r + "," + g + "," + b;
}

// 計算色彩校正矩陣
function calculateColorMatrix(points) {
    print("計算色彩校正矩陣...");
    
    // 解析色彩點資料
    sourceColors = newArray(points.length * 3);
    targetColors = newArray(points.length * 3);
    
    for (i = 0; i < points.length; i++) {
        parts = split(points[i], ":");
        colorName = parts[0];
        actualRGB = split(parts[2], ",");
        
        // 獲取標準色彩
        standardRGB = getStandardColor(colorName);
        
        // 填充矩陣
        sourceColors[i*3] = parseInt(actualRGB[0]) / 255.0;
        sourceColors[i*3+1] = parseInt(actualRGB[1]) / 255.0;
        sourceColors[i*3+2] = parseInt(actualRGB[2]) / 255.0;
        
        targetColors[i*3] = parseInt(split(standardRGB, ",")[0]) / 255.0;
        targetColors[i*3+1] = parseInt(split(standardRGB, ",")[1]) / 255.0;
        targetColors[i*3+2] = parseInt(split(standardRGB, ",")[2]) / 255.0;
    }
    
    // 簡化的色彩矩陣計算（對角矩陣）
    matrix = newArray(9);
    for (i = 0; i < 3; i++) {
        if (sourceColors[i] > 0.001) {
            matrix[i*3+i] = targetColors[i] / sourceColors[i];
        } else {
            matrix[i*3+i] = 1.0;
        }
        // 非對角元素設為0
        for (j = 0; j < 3; j++) {
            if (i != j) {
                matrix[i*3+j] = 0.0;
            }
        }
    }
    
    print("色彩校正矩陣:");
    print("  [" + matrix[0] + ", " + matrix[1] + ", " + matrix[2] + "]");
    print("  [" + matrix[3] + ", " + matrix[4] + ", " + matrix[5] + "]");
    print("  [" + matrix[6] + ", " + matrix[7] + ", " + matrix[8] + "]");
    
    return matrix;
}

// 獲取標準色彩
function getStandardColor(colorName) {
    if (colorName == "Red") return "255,0,0";
    if (colorName == "Yellow") return "255,255,0";
    if (colorName == "Green") return "0,255,0";
    if (colorName == "Blue") return "0,0,255";
    return grayValue; // 灰色
}

// 應用色彩校正
function applyColorCorrection(matrix) {
    print("應用色彩校正...");
    
    currentImage = getImageID();
    
    // 分離RGB通道
    run("Split Channels");
    
    redID = getImageID("[C1]");
    greenID = getImageID("[C2]");
    blueID = getImageID("[C3]");
    
    // 應用矩陣變換到每個通道
    selectImage(redID);
    run("32-bit");
    run("Multiply...", "value=" + matrix[0]);
    
    selectImage(greenID);
    run("32-bit");
    run("Multiply...", "value=" + matrix[4]);
    
    selectImage(blueID);
    run("32-bit");
    run("Multiply...", "value=" + matrix[8]);
    
    // 合併通道
    run("Merge Channels...", 
        "c1=[C1] c2=[C2] c3=[C3] create");
    
    // 轉回8位
    run("8-bit");
    
    rename("Color_Corrected");
    
    print("色彩校正完成");
}

// 驗證校正效果
function validateCorrection() {
    print("驗證校正效果...");
    
    // 重新檢測色彩點並計算色差
    correctedPoints = detectColorPoints();
    
    totalDelta = 0;
    validPoints = 0;
    
    for (i = 0; i < correctedPoints.length; i++) {
        parts = split(correctedPoints[i], ":");
        colorName = parts[0];
        actualRGB = split(parts[2], ",");
        standardRGB = split(getStandardColor(colorName), ",");
        
        // 計算色差（簡化的歐氏距離）
        deltaE = sqrt(pow(parseInt(actualRGB[0]) - parseInt(split(standardRGB, ",")[0]), 2) +
                     pow(parseInt(actualRGB[1]) - parseInt(split(standardRGB, ",")[1]), 2) +
                     pow(parseInt(actualRGB[2]) - parseInt(split(standardRGB, ",")[2]), 2));
        
        totalDelta += deltaE;
        validPoints++;
        
        print("  " + colorName + " 色差: ΔE = " + deltaE);
    }
    
    if (validPoints > 0) {
        avgDelta = totalDelta / validPoints;
        print("平均色差: ΔE = " + avgDelta);
        
        if (avgDelta < 10) {
            print("色彩校正品質: 優秀");
        } else if (avgDelta < 20) {
            print("色彩校正品質: 良好");
        } else {
            print("色彩校正品質: 需要改進");
        }
    }
}

// 顯示結果
function showResults() {
    // 創建結果視窗並排顯示
    selectImage(originalID);
    run("Duplicate...", "title=Original");
    
    selectWindow("Color_Corrected");
    
    // 並排顯示
    run("Images to Stack", "name=Comparison title=[] use");
    run("Make Montage...", "columns=2 rows=1 scale=0.5");
    
    rename("Before_After_Comparison");
    
    showMessage("色彩校正完成", 
        "校正前後對比已顯示\n" +
        "詳細結果請查看Log視窗");
}

// 批次處理函數
macro "Batch Color Calibration" {
    inputDir = getDirectory("選擇輸入資料夾");
    outputDir = getDirectory("選擇輸出資料夾");
    
    fileList = getFileList(inputDir);
    imageCount = 0;
    
    for (i = 0; i < fileList.length; i++) {
        if (endsWith(fileList[i], ".jpg") || endsWith(fileList[i], ".png") || 
            endsWith(fileList[i], ".tif")) {
            
            print("處理: " + fileList[i]);
            
            open(inputDir + fileList[i]);
            originalTitle = getTitle();
            
            // 執行色彩校正
            if (detectSquareSticker()) {
                detectAndCorrectPerspective();
                colorPoints = detectColorPoints();
                
                if (colorPoints.length >= 3) {
                    colorMatrix = calculateColorMatrix(colorPoints);
                    applyColorCorrection(colorMatrix);
                    
                    // 儲存結果
                    saveAs("JPEG", outputDir + "corrected_" + fileList[i]);
                    imageCount++;
                } else {
                    print("  跳過: 色彩點不足");
                }
            } else {
                print("  跳過: 未檢測到校正貼紙");
            }
            
            // 關閉所有圖像
            run("Close All");
        }
    }
    
    showMessage("批次處理完成", 
        "已處理 " + imageCount + " 張圖像\n" +
        "結果已儲存至: " + outputDir);
}

print("ImageJ色彩校正Macro v2.0 載入完成");
print("使用方法:");
print("  1. Auto Color Calibration - 單張圖像校正");
print("  2. Batch Color Calibration - 批次處理");
print("確保圖像包含完整的方形RGBY校正貼紙");