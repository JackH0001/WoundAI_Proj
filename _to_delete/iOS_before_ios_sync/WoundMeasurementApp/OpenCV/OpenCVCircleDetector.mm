// 模擬器 arm64（Apple Silicon）無對應 OpenCV slice 時降級為安全 stub
#import "OpenCVCircleDetector.h"
#import <UIKit/UIKit.h>

#include <TargetConditionals.h>
#if TARGET_OS_SIMULATOR && !defined(__x86_64__)
#define OCV_SIM_ARM64_STUB 1
#else
#define OCV_SIM_ARM64_STUB 0
#endif

// 針對模擬器架構的條件編譯
#if TARGET_OS_SIMULATOR && defined(__arm64__)
// Apple Silicon 模擬器特殊處理
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdocumentation"
#endif

#if !OCV_SIM_ARM64_STUB
  #if __has_include(<opencv2/imgproc.hpp>)
    #import <opencv2/imgproc.hpp>
  #else
    #import "opencv2/imgproc.hpp"
  #endif
#endif

#if TARGET_OS_SIMULATOR && defined(__arm64__)
#pragma clang diagnostic pop
#endif

#if !OCV_SIM_ARM64_STUB
using namespace cv;
#endif

@implementation OpenCVCircleDetector

// 本地 UIImage/Mat 互轉：避免依賴 ios.h 的符號（UIImageToMat/MatToUIImage）
#if !OCV_SIM_ARM64_STUB
static void OCVUIImageToMatLocal(UIImage *image, cv::Mat &outMat) {
    if (!image) { outMat.release(); return; }
    CGImageRef cgImage = image.CGImage;
    if (!cgImage) { outMat.release(); return; }
    const size_t width = CGImageGetWidth(cgImage);
    const size_t height = CGImageGetHeight(cgImage);
    cv::Mat rgba((int)height, (int)width, CV_8UC4);
    CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
    CGBitmapInfo bitmapInfo = kCGImageAlphaPremultipliedLast | kCGBitmapByteOrderDefault;
    CGContextRef context = CGBitmapContextCreate(rgba.data,
                                                 width,
                                                 height,
                                                 8,
                                                 rgba.step[0],
                                                 colorSpace,
                                                 bitmapInfo);
    if (context) {
        CGContextDrawImage(context, CGRectMake(0, 0, width, height), cgImage);
        CGContextRelease(context);
        outMat = rgba;
    } else {
        outMat.release();
    }
    CGColorSpaceRelease(colorSpace);
}

static UIImage *OCVMatToUIImageLocal(const cv::Mat &matInput) {
    if (matInput.empty()) { return nil; }
    cv::Mat rgba;
    switch (matInput.type()) {
        case CV_8UC1:
            cv::cvtColor(matInput, rgba, cv::COLOR_GRAY2RGBA);
            break;
        case CV_8UC3:
            cv::cvtColor(matInput, rgba, cv::COLOR_BGR2RGBA);
            break;
        case CV_8UC4: {
            rgba = matInput.clone();
            break;
        }
        default:
            return nil;
    }
    const size_t width = (size_t)rgba.cols;
    const size_t height = (size_t)rgba.rows;
    const size_t bytesPerRow = (size_t)rgba.step[0];
    const size_t dataSize = bytesPerRow * height;
    void *dataCopy = malloc(dataSize);
    if (!dataCopy) { return nil; }
    memcpy(dataCopy, rgba.data, dataSize);

    CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef provider = CGDataProviderCreateWithData(NULL, dataCopy, dataSize, [](void *info, const void *data, size_t size){ free((void*)data); });
    CGBitmapInfo bitmapInfo = kCGImageAlphaPremultipliedLast | kCGBitmapByteOrderDefault;
    CGImageRef cgImage = CGImageCreate(width,
                                       height,
                                       8,
                                       32,
                                       bytesPerRow,
                                       colorSpace,
                                       bitmapInfo,
                                       provider,
                                       NULL,
                                       false,
                                       kCGRenderingIntentDefault);
    UIImage *image = cgImage ? [UIImage imageWithCGImage:cgImage scale:1.0 orientation:UIImageOrientationUp] : nil;
    if (cgImage) CGImageRelease(cgImage);
    CGDataProviderRelease(provider);
    CGColorSpaceRelease(colorSpace);
    return image;
}
#endif

+ (NSArray<NSValue*> *)detectCircles:(CGImageRef)image
                                   minRadius:(int)minRadius
                                   maxRadius:(int)maxRadius
                                     dpRatio:(double)dp
                              minDistBetween:(double)minDist
                               cannyThreshold:(double)cannyThresh
                             accumulatorThreshold:(double)accThresh
                                         topN:(NSUInteger)topN {
#if OCV_SIM_ARM64_STUB
    // 模擬器 arm64 stub：返回空結果，避免連結 OpenCV 符號
    if (!image) { return @[]; }
    return @[];
#else
    if (!image) { return @[]; }

    // 基本參數防呆與修正
    if (dp <= 0.0) { dp = 1.0; }
    dp = std::max(0.5, std::min(dp, 2.5));
    if (minDist <= 0.0) { minDist = 10.0; }
    if (cannyThresh <= 0.0) { cannyThresh = 100.0; }
    if (accThresh <= 0.0) { accThresh = 30.0; }
    if ((int)topN <= 0) { topN = 1; }

    // 半徑參數處理：若不合法則放寬，交由 OpenCV 自動決定
    if (minRadius < 0) { minRadius = 0; }
    if (maxRadius < 0) { maxRadius = 0; }
    if (maxRadius > 0 && minRadius > maxRadius) {
        std::swap(minRadius, maxRadius);
    }

    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) { return @[]; }

    // 轉灰階
    cv::Mat gray;
    if (mat.channels() == 3) {
        cv::cvtColor(mat, gray, cv::COLOR_BGR2GRAY);
    } else if (mat.channels() == 4) {
        cv::cvtColor(mat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = mat;
    }

    // 影像過大時先縮小，避免計算時間過長
    const int desiredMaxDim = 1600; // 可視硬體調整
    double scale = 1.0;
    if (gray.cols > desiredMaxDim || gray.rows > desiredMaxDim) {
        double scaleX = static_cast<double>(desiredMaxDim) / static_cast<double>(gray.cols);
        double scaleY = static_cast<double>(desiredMaxDim) / static_cast<double>(gray.rows);
        scale = std::min(scaleX, scaleY);
        cv::Mat resized;
        cv::resize(gray, resized, cv::Size(), scale, scale, cv::INTER_AREA);
        gray = resized;
        if (minRadius > 0) { minRadius = static_cast<int>(std::round(minRadius * scale)); }
        if (maxRadius > 0) { maxRadius = static_cast<int>(std::round(maxRadius * scale)); }
        if (minDist > 0) { minDist = std::max(1.0, minDist * scale); }
    }

    // 前處理：CLAHE + 輕度平滑 + 輕度銳化 + normalize
    {
        cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8,8));
        clahe->apply(gray, gray);
        cv::GaussianBlur(gray, gray, cv::Size(5, 5), 1.2);
        // unsharp mask
        cv::Mat blur;
        cv::GaussianBlur(gray, blur, cv::Size(0,0), 2.0);
        cv::addWeighted(gray, 1.5, blur, -0.5, 0, gray);
        cv::normalize(gray, gray, 0, 255, cv::NORM_MINMAX);
    }

    auto runHough = [&](double dpLocal, double cannyLocal, double accLocal, int minRLocal, int maxRLocal){
        std::vector<cv::Vec3f> out;
        cv::HoughCircles(gray, out, cv::HOUGH_GRADIENT, dpLocal, std::max(1.0, minDist), cannyLocal, accLocal, minRLocal, maxRLocal);
        return out;
    };
    std::vector<cv::Vec3f> circles = runHough(dp, cannyThresh, accThresh, minRadius, maxRadius);
    if (circles.empty()) {
        // 多尺度重試
        std::vector<double> scales = {0.75, 0.5};
        for (double s : scales) {
            cv::Mat tmp; cv::resize(gray, tmp, cv::Size(), s, s, cv::INTER_AREA);
            cv::Mat grayBackup = gray; gray = tmp;
            int minR2 = minRadius>0 ? std::max(1, (int)std::round(minRadius*s)) : 0;
            int maxR2 = maxRadius>0 ? std::max(minR2+1, (int)std::round(maxRadius*s)) : 0;
            auto alt = runHough(std::max(0.8, dp*0.9), std::max(50.0, cannyThresh*0.9), std::max(20.0, accThresh*0.85), minR2, maxR2);
            if (!alt.empty()) {
                // 還原座標
                double inv = (scale>0.0?1.0/scale:1.0) * (1.0/s);
                for (auto &c : alt) {
                    c[0] *= inv; c[1] *= inv; c[2] *= inv;
                }
                circles = alt;
                gray = grayBackup;
                break;
            }
            gray = grayBackup;
        }
    }

    if (circles.empty()) { return @[]; }

    // 依半徑大小排序（大到小），有助於優先挑選主要貼紙或較明顯圓
    std::sort(circles.begin(), circles.end(), [](const cv::Vec3f &a, const cv::Vec3f &b) {
        return a[2] > b[2];
    });

    // 進一步過濾：圓內外對比與邊界梯度一致性，提升穩定性
    auto passesContrastAndEdge = [&](const cv::Vec3f &c)->bool {
        int cx = (int)std::round(c[0]);
        int cy = (int)std::round(c[1]);
        int r  = (int)std::round(c[2]);
        if (cx < 0 || cy < 0 || cx >= gray.cols || cy >= gray.rows || r < 4) return false;

        int innerR = std::max(1, (int)std::round(r * 0.6));
        int outerR = std::min((int)std::round(r * 1.2), std::min(gray.cols, gray.rows) - 1);

        // 計算內環與外環平均亮度
        auto ringMean = [&](int r1, int r2)->double {
            double sum = 0.0; int cnt = 0;
            for (int ang = 0; ang < 360; ang += 6) {
                double t = ang * CV_PI / 180.0;
                int x1 = cx + (int)std::round(r1 * std::cos(t));
                int y1 = cy + (int)std::round(r1 * std::sin(t));
                int x2 = cx + (int)std::round(r2 * std::cos(t));
                int y2 = cy + (int)std::round(r2 * std::sin(t));
                if (x1>=0 && x1<gray.cols && y1>=0 && y1<gray.rows && x2>=0 && x2<gray.cols && y2>=0 && y2<gray.rows) {
                    sum += gray.at<uchar>(y1,x1);
                    sum += gray.at<uchar>(y2,x2);
                    cnt += 2;
                }
            }
            return cnt>0 ? sum / cnt : 0.0;
        };

        double innerMean = ringMean(std::max(1, r/3), innerR);
        double outerMean = ringMean(outerR, std::min(outerR+2, std::min(gray.cols, gray.rows)-1));
        double contrast = std::abs(innerMean - outerMean);

        // 邊界梯度量測
        int edgeHits = 0, samples = 0;
        for (int ang = 0; ang < 360; ang += 4) {
            double t = ang * CV_PI / 180.0;
            int x = cx + (int)std::round(r * std::cos(t));
            int y = cy + (int)std::round(r * std::sin(t));
            int xOut = cx + (int)std::round((r+2) * std::cos(t));
            int yOut = cy + (int)std::round((r+2) * std::sin(t));
            int xIn  = cx + (int)std::round((r-2) * std::cos(t));
            int yIn  = cy + (int)std::round((r-2) * std::sin(t));
            if (xIn>=0 && xIn<gray.cols && yIn>=0 && yIn<gray.rows && xOut>=0 && xOut<gray.cols && yOut>=0 && yOut<gray.rows) {
                int gIn = gray.at<uchar>(yIn,xIn);
                int gOut = gray.at<uchar>(yOut,xOut);
                if (std::abs(gIn - gOut) > 10) edgeHits++;
                samples++;
            }
        }
        double edgeConsistency = samples>0 ? (double)edgeHits / samples : 0.0;

        return (contrast >= 6.0) && (edgeConsistency >= 0.30);
    };

    std::vector<cv::Vec3f> filtered;
    filtered.reserve(circles.size());
    for (const auto &c : circles) {
        if (passesContrastAndEdge(c)) filtered.push_back(c);
    }
    if (filtered.empty()) filtered = circles; // 若過濾過嚴則回退

    NSMutableArray<NSValue*> *out = [NSMutableArray array];
    size_t count = std::min((size_t)topN, filtered.size());
    for (size_t i = 0; i < count; ++i) {
        const cv::Vec3f &c = filtered[i];
        // 將座標、半徑還原回原圖比例
        CGFloat invScale = (scale > 0.0) ? (1.0 / scale) : 1.0;
        CGPoint center = CGPointMake(c[0] * invScale, c[1] * invScale);
        CGFloat r = c[2] * invScale;
        CGRect packed = CGRectMake(center.x, center.y, r, 0);
        [out addObject:[NSValue valueWithCGRect:packed]];
    }
    return out;
#endif
}

// 舊版相容方法：轉呼叫新版
+ (NSArray<NSValue*> *)detectCirclesInCGImage:(CGImageRef)image
                                   minRadius:(int)minRadius
                                   maxRadius:(int)maxRadius
                                     dpRatio:(double)dp
                              minDistBetween:(double)minDist
                               cannyThreshold:(double)cannyThresh
                             accumulatorThreshold:(double)accThresh
                                         topN:(NSUInteger)topN {
#if OCV_SIM_ARM64_STUB
    if (!image) { return @[]; }
    return @[];
#else
    return [self detectCircles:image
                     minRadius:minRadius
                     maxRadius:maxRadius
                       dpRatio:dp
                minDistBetween:minDist
                 cannyThreshold:cannyThresh
           accumulatorThreshold:accThresh
                           topN:topN];
    return [self detectCircles:image
                     minRadius:minRadius
                     maxRadius:maxRadius
                       dpRatio:dp
                minDistBetween:minDist
                 cannyThreshold:cannyThresh
           accumulatorThreshold:accThresh
                           topN:topN];
#endif
}

// 多通道增強檢測實作
+ (NSArray<NSValue*> *)detectCirclesMultiChannel:(CGImageRef)image
                                       minRadius:(int)minRadius
                                       maxRadius:(int)maxRadius
                                         dpRatio:(double)dp
                                  minDistBetween:(double)minDist
                                   cannyThreshold:(double)cannyThresh
                                 accumulatorThreshold:(double)accThresh
                                             topN:(NSUInteger)topN
                                       useLabChannel:(BOOL)useLab
                                       useHSVChannel:(BOOL)useHSV
                                    useRGBChannels:(BOOL)useRGB
                                    parameterSweep:(BOOL)enableSweep {
#if OCV_SIM_ARM64_STUB
    if (!image) { return @[]; }
    return @[];
#else
    if (!image) { return @[]; }
    
    // 基本參數驗證
    if (dp <= 0.0) { dp = 1.0; }
    dp = std::max(0.5, std::min(dp, 2.5));
    if (minDist <= 0.0) { minDist = 10.0; }
    if (cannyThresh <= 0.0) { cannyThresh = 100.0; }
    if (accThresh <= 0.0) { accThresh = 30.0; }
    if ((int)topN <= 0) { topN = 1; }

    // 半徑參數處理
    if (minRadius < 0) { minRadius = 0; }
    if (maxRadius < 0) { maxRadius = 0; }
    if (maxRadius > 0 && minRadius > maxRadius) {
        std::swap(minRadius, maxRadius);
    }

    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) { return @[]; }

    // 圖像縮放處理
    const int desiredMaxDim = 1600;
    double scale = 1.0;
    if (mat.cols > desiredMaxDim || mat.rows > desiredMaxDim) {
        double scaleX = static_cast<double>(desiredMaxDim) / static_cast<double>(mat.cols);
        double scaleY = static_cast<double>(desiredMaxDim) / static_cast<double>(mat.rows);
        scale = std::min(scaleX, scaleY);
        cv::Mat resized;
        cv::resize(mat, resized, cv::Size(), scale, scale, cv::INTER_AREA);
        mat = resized;
        if (minRadius > 0) { minRadius = static_cast<int>(std::round(minRadius * scale)); }
        if (maxRadius > 0) { maxRadius = static_cast<int>(std::round(maxRadius * scale)); }
        if (minDist > 0) { minDist = std::max(1.0, minDist * scale); }
    }

    std::vector<cv::Vec3f> allCircles;
    
    // 多通道檢測執行函數
    auto runHoughOnChannel = [&](const cv::Mat& channel, const std::string& channelName) {
        cv::Mat processed = channel.clone();
        
        // 通道特定前處理
        cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8,8));
        clahe->apply(processed, processed);
        cv::GaussianBlur(processed, processed, cv::Size(5, 5), 1.2);
        
        // 輕度銳化
        cv::Mat blur;
        cv::GaussianBlur(processed, blur, cv::Size(0,0), 2.0);
        cv::addWeighted(processed, 1.5, blur, -0.5, 0, processed);
        cv::normalize(processed, processed, 0, 255, cv::NORM_MINMAX);
        
        std::vector<cv::Vec3f> circles;
        cv::HoughCircles(processed, circles, cv::HOUGH_GRADIENT, dp, std::max(1.0, minDist), 
                        cannyThresh, accThresh, minRadius, maxRadius);
        
        if (!circles.empty()) {
            printf("通道 %s: 檢測到 %zu 個圓形\n", channelName.c_str(), circles.size());
        }
        
        return circles;
    };
    
    // 參數掃描函數
    auto parameterSweepOnChannel = [&](const cv::Mat& channel, const std::string& channelName) {
        std::vector<cv::Vec3f> bestCircles;
        double bestScore = 0.0;
        
        // 參數掃描範圍
        std::vector<double> dpValues = {dp, dp * 0.8, dp * 1.2};
        std::vector<double> cannyValues = {cannyThresh, cannyThresh * 0.7, cannyThresh * 1.3};
        std::vector<double> accValues = {accThresh, accThresh * 0.8, accThresh * 1.2};
        
        for (double dpVal : dpValues) {
            for (double cannyVal : cannyValues) {
                for (double accVal : accValues) {
                    std::vector<cv::Vec3f> circles;
                    cv::Mat processed = channel.clone();
                    
                    cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8,8));
                    clahe->apply(processed, processed);
                    cv::GaussianBlur(processed, processed, cv::Size(5, 5), 1.2);
                    cv::normalize(processed, processed, 0, 255, cv::NORM_MINMAX);
                    
                    cv::HoughCircles(processed, circles, cv::HOUGH_GRADIENT, dpVal, std::max(1.0, minDist), 
                                    cannyVal, accVal, minRadius, maxRadius);
                    
                    // 簡單評分：圓形數量 + 半徑一致性
                    if (!circles.empty()) {
                        double score = circles.size();
                        if (circles.size() >= 3) {
                            // 獎勵半徑一致性
                            double avgRadius = 0.0;
                            for (const auto& c : circles) avgRadius += c[2];
                            avgRadius /= circles.size();
                            
                            double radiusVariance = 0.0;
                            for (const auto& c : circles) {
                                radiusVariance += std::pow(c[2] - avgRadius, 2);
                            }
                            radiusVariance /= circles.size();
                            score += 1.0 / (1.0 + radiusVariance); // 方差越小分數越高
                        }
                        
                        if (score > bestScore) {
                            bestScore = score;
                            bestCircles = circles;
                        }
                    }
                }
            }
        }
        
        if (!bestCircles.empty()) {
            printf("通道 %s 參數掃描: 最佳分數 %.2f，圓形數量 %zu\n", 
                   channelName.c_str(), bestScore, bestCircles.size());
        }
        
        return bestCircles;
    };
    
    // 1. 標準灰階檢測
    cv::Mat gray;
    if (mat.channels() == 3) {
        cv::cvtColor(mat, gray, cv::COLOR_BGR2GRAY);
    } else if (mat.channels() == 4) {
        cv::cvtColor(mat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = mat;
    }
    
    auto grayCircles = enableSweep ? parameterSweepOnChannel(gray, "Gray") : runHoughOnChannel(gray, "Gray");
    allCircles.insert(allCircles.end(), grayCircles.begin(), grayCircles.end());
    
    // 2. Lab L通道檢測
    if (useLab && mat.channels() >= 3) {
        cv::Mat lab, labChannels[3];
        cv::cvtColor(mat, lab, cv::COLOR_BGR2Lab);
        cv::split(lab, labChannels);
        
        auto labCircles = enableSweep ? parameterSweepOnChannel(labChannels[0], "Lab-L") : runHoughOnChannel(labChannels[0], "Lab-L");
        allCircles.insert(allCircles.end(), labCircles.begin(), labCircles.end());
    }
    
    // 3. HSV V通道檢測
    if (useHSV && mat.channels() >= 3) {
        cv::Mat hsv, hsvChannels[3];
        cv::cvtColor(mat, hsv, cv::COLOR_BGR2HSV);
        cv::split(hsv, hsvChannels);
        
        auto hsvCircles = enableSweep ? parameterSweepOnChannel(hsvChannels[2], "HSV-V") : runHoughOnChannel(hsvChannels[2], "HSV-V");
        allCircles.insert(allCircles.end(), hsvCircles.begin(), hsvCircles.end());
    }
    
    // 4. RGB單通道檢測
    if (useRGB && mat.channels() >= 3) {
        cv::Mat bgrChannels[3];
        cv::split(mat, bgrChannels);
        
        // B, G, R 通道分別檢測
        std::string channelNames[] = {"Blue", "Green", "Red"};
        for (int i = 0; i < 3; i++) {
            auto rgbCircles = enableSweep ? parameterSweepOnChannel(bgrChannels[i], channelNames[i]) : runHoughOnChannel(bgrChannels[i], channelNames[i]);
            allCircles.insert(allCircles.end(), rgbCircles.begin(), rgbCircles.end());
        }
    }
    
    if (allCircles.empty()) { return @[]; }
    
    // 去重和合併相近的圓
    std::vector<cv::Vec3f> mergedCircles;
    const double mergeThreshold = std::max(10.0, minDist * 0.5);
    
    for (const auto& circle : allCircles) {
        bool merged = false;
        for (auto& existing : mergedCircles) {
            double dist = std::sqrt(std::pow(circle[0] - existing[0], 2) + std::pow(circle[1] - existing[1], 2));
            if (dist < mergeThreshold) {
                // 合併：取平均
                existing[0] = (existing[0] + circle[0]) * 0.5;
                existing[1] = (existing[1] + circle[1]) * 0.5;
                existing[2] = (existing[2] + circle[2]) * 0.5;
                merged = true;
                break;
            }
        }
        if (!merged) {
            mergedCircles.push_back(circle);
        }
    }
    
    // 按半徑排序
    std::sort(mergedCircles.begin(), mergedCircles.end(), [](const cv::Vec3f &a, const cv::Vec3f &b) {
        return a[2] > b[2];
    });
    
    // 轉換輸出格式
    NSMutableArray<NSValue*> *out = [NSMutableArray array];
    size_t count = std::min((size_t)topN, mergedCircles.size());
    for (size_t i = 0; i < count; ++i) {
        const cv::Vec3f &c = mergedCircles[i];
        CGFloat invScale = (scale > 0.0) ? (1.0 / scale) : 1.0;
        CGPoint center = CGPointMake(c[0] * invScale, c[1] * invScale);
        CGFloat r = c[2] * invScale;
        CGRect packed = CGRectMake(center.x, center.y, r, 0);
        [out addObject:[NSValue valueWithCGRect:packed]];
    }
    
    printf("多通道檢測完成：總共檢測到 %zu 個圓形，合併後 %zu 個，輸出 %zu 個\n", 
           allCircles.size(), mergedCircles.size(), count);
    
    return out;
#endif
}

@end

// =============================
// OpenCVSquareDetector 實作  
// =============================

@implementation OpenCVSquareDetector

// 檢測方形邊界
+ (NSArray<NSValue*> *)detectSquares:(CGImageRef)image
                          minSize:(double)minSize
                          maxSize:(double)maxSize
                       aspectRatio:(double)targetRatio
                         tolerance:(double)tolerance
                              topN:(NSUInteger)topN {
#if OCV_SIM_ARM64_STUB
    if (!image) { return @[]; }
    return @[];
#else
    if (!image) { return @[]; }
    
    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) { return @[]; }
    
    // 轉灰階
    cv::Mat gray;
    if (mat.channels() == 3) {
        cv::cvtColor(mat, gray, cv::COLOR_BGR2GRAY);
    } else if (mat.channels() == 4) {
        cv::cvtColor(mat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = mat;
    }
    
    // 邊緣檢測
    cv::Mat edges;
    cv::GaussianBlur(gray, gray, cv::Size(5, 5), 1.0);
    cv::Canny(gray, edges, 50, 150);
    
    // 尋找輪廓
    std::vector<std::vector<cv::Point>> contours;
    std::vector<cv::Vec4i> hierarchy;
    cv::findContours(edges, contours, hierarchy, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
    
    std::vector<cv::Rect> squares;
    
    for (const auto& contour : contours) {
        // 多邊形近似
        std::vector<cv::Point> approx;
        double epsilon = 0.02 * cv::arcLength(contour, true);
        cv::approxPolyDP(contour, approx, epsilon, true);
        
        // 檢查是否為四邊形
        if (approx.size() != 4) continue;
        
        // 檢查是否為凸多邊形
        if (!cv::isContourConvex(approx)) continue;
        
        cv::Rect boundingRect = cv::boundingRect(approx);
        
        // 檢查尺寸
        double size = std::max(boundingRect.width, boundingRect.height);
        if (size < minSize || size > maxSize) continue;
        
        // 檢查長寬比
        double aspectRatio = (double)boundingRect.width / boundingRect.height;
        if (std::abs(aspectRatio - targetRatio) > tolerance) continue;
        
        squares.push_back(boundingRect);
    }
    
    // 按面積排序
    std::sort(squares.begin(), squares.end(), [](const cv::Rect& a, const cv::Rect& b) {
        return a.area() > b.area();
    });
    
    // 轉換輸出格式
    NSMutableArray<NSValue*> *result = [NSMutableArray array];
    size_t count = std::min((size_t)topN, squares.size());
    for (size_t i = 0; i < count; i++) {
        const cv::Rect& rect = squares[i];
        CGRect cgRect = CGRectMake(rect.x, rect.y, rect.width, rect.height);
        [result addObject:[NSValue valueWithCGRect:cgRect]];
    }
    
    printf("方形檢測: 找到 %zu 個候選方形，輸出 %zu 個\n", squares.size(), count);
    return result;
#endif
}

// 檢測四角凸點
+ (NSArray<NSValue*> *)detectCornerDots:(CGImageRef)image
                                inRegion:(CGRect)region
                               minRadius:(int)minRadius
                               maxRadius:(int)maxRadius
                                    topN:(NSUInteger)topN {
#if OCV_SIM_ARM64_STUB
    if (!image) { return @[]; }
    return @[];
#else
    if (!image) { return @[]; }
    
    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) { return @[]; }
    
    // 裁切感興趣區域
    cv::Rect roi(region.origin.x, region.origin.y, region.size.width, region.size.height);
    roi = roi & cv::Rect(0, 0, mat.cols, mat.rows); // 確保在圖像範圍內
    if (roi.area() <= 0) { return @[]; }
    
    cv::Mat roiMat = mat(roi);
    
    // 轉灰階
    cv::Mat gray;
    if (roiMat.channels() == 3) {
        cv::cvtColor(roiMat, gray, cv::COLOR_BGR2GRAY);
    } else if (roiMat.channels() == 4) {
        cv::cvtColor(roiMat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = roiMat;
    }
    
    // 使用HoughCircles檢測小圓點
    std::vector<cv::Vec3f> circles;
    cv::HoughCircles(gray, circles, cv::HOUGH_GRADIENT, 1, minRadius * 2,
                    100, 30, minRadius, maxRadius);
    
    // 過濾角落位置的點
    std::vector<cv::Vec3f> cornerCircles;
    for (const auto& circle : circles) {
        float x = circle[0];
        float y = circle[1];
        
        // 檢查是否在角落附近
        bool isCorner = false;
        float margin = maxRadius * 2;
        
        if ((x < margin && y < margin) ||  // 左上角
            (x > gray.cols - margin && y < margin) ||  // 右上角
            (x < margin && y > gray.rows - margin) ||  // 左下角
            (x > gray.cols - margin && y > gray.rows - margin)) {  // 右下角
            isCorner = true;
        }
        
        if (isCorner) {
            cornerCircles.push_back(circle);
        }
    }
    
    // 按半徑排序
    std::sort(cornerCircles.begin(), cornerCircles.end(), [](const cv::Vec3f& a, const cv::Vec3f& b) {
        return a[2] > b[2];
    });
    
    // 轉換輸出格式（轉回原圖座標系）
    NSMutableArray<NSValue*> *result = [NSMutableArray array];
    size_t count = std::min((size_t)topN, cornerCircles.size());
    for (size_t i = 0; i < count; i++) {
        const cv::Vec3f& c = cornerCircles[i];
        CGPoint center = CGPointMake(c[0] + roi.x, c[1] + roi.y);
        CGFloat radius = c[2];
        CGRect packed = CGRectMake(center.x, center.y, radius, 0);
        [result addObject:[NSValue valueWithCGRect:packed]];
    }
    
    printf("角點檢測: 在區域內找到 %zu 個角點\n", count);
    return result;
#endif
}

// RGBY色彩點檢測
+ (NSArray<NSDictionary*> *)detectColorPoints:(CGImageRef)image
                                     inRegion:(CGRect)region
                                   colorNames:(NSArray<NSString*>*)colorNames
                                  hsvRanges:(NSArray<NSArray<NSNumber*>*>*)ranges {
#if OCV_SIM_ARM64_STUB
    if (!image || colorNames.count != ranges.count) { return @[]; }
    return @[];
#else
    if (!image || colorNames.count != ranges.count) { return @[]; }
    
    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) { return @[]; }
    
    // 裁切感興趣區域
    cv::Rect roi(region.origin.x, region.origin.y, region.size.width, region.size.height);
    roi = roi & cv::Rect(0, 0, mat.cols, mat.rows);
    if (roi.area() <= 0) { return @[]; }
    
    cv::Mat roiMat = mat(roi);
    
    // 轉換為HSV
    cv::Mat hsv;
    cv::cvtColor(roiMat, hsv, cv::COLOR_BGR2HSV);
    
    NSMutableArray<NSDictionary*> *result = [NSMutableArray array];
    
    for (NSUInteger i = 0; i < colorNames.count; i++) {
        NSString *colorName = colorNames[i];
        NSArray<NSNumber*> *range = ranges[i];
        
        if (range.count < 6) continue;
        
        // 創建HSV遮罩
        cv::Scalar lower(range[0].floatValue, range[1].floatValue, range[2].floatValue);
        cv::Scalar upper(range[3].floatValue, range[4].floatValue, range[5].floatValue);
        
        cv::Mat mask;
        cv::inRange(hsv, lower, upper, mask);
        
        // 找到最大連通區域
        std::vector<std::vector<cv::Point>> contours;
        cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
        
        if (!contours.empty()) {
            // 找最大輪廓
            auto maxContour = *std::max_element(contours.begin(), contours.end(),
                [](const std::vector<cv::Point>& a, const std::vector<cv::Point>& b) {
                    return cv::contourArea(a) < cv::contourArea(b);
                });
            
            cv::Moments moments = cv::moments(maxContour);
            if (moments.m00 > 0) {
                CGPoint center = CGPointMake(
                    moments.m10 / moments.m00 + roi.x,
                    moments.m01 / moments.m00 + roi.y
                );
                
                // 獲取該位置的實際BGR顏色
                cv::Point2i pixelPos(moments.m10 / moments.m00, moments.m01 / moments.m00);
                if (pixelPos.x >= 0 && pixelPos.x < roiMat.cols && 
                    pixelPos.y >= 0 && pixelPos.y < roiMat.rows) {
                    
                    cv::Vec3b bgr = roiMat.at<cv::Vec3b>(pixelPos);
                    
                    NSDictionary *colorPoint = @{
                        @"colorName": colorName,
                        @"position": [NSValue valueWithCGPoint:center],
                        @"actualColor": @[@(bgr[2]/255.0), @(bgr[1]/255.0), @(bgr[0]/255.0)], // BGR->RGB
                        @"confidence": @(cv::contourArea(maxContour) / (roi.area()))
                    };
                    
                    [result addObject:colorPoint];
                }
            }
        }
    }
    
    printf("色彩點檢測: 檢測到 %lu 個色彩點\n", (unsigned long)result.count);
    return result;
#endif
}

// 透視變換校正
+ (CGImageRef)applyPerspectiveCorrection:(CGImageRef)image
                             cornerPoints:(NSArray<NSValue*>*)corners
                               targetSize:(CGSize)targetSize {
#if OCV_SIM_ARM64_STUB
    if (image) CFRetain(image);
    return image;
#else
    if (!image || corners.count != 4) {
        CFRetain(image);
        return image;
    }
    
    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) {
        CFRetain(image);
        return image;
    }
    
    // 解析角點
    std::vector<cv::Point2f> srcPoints;
    for (NSValue *cornerValue in corners) {
        CGRect rect = cornerValue.CGRectValue;
        srcPoints.push_back(cv::Point2f(rect.origin.x, rect.origin.y));
    }
    
    // 目標點（正方形的四個角）
    std::vector<cv::Point2f> dstPoints = {
        cv::Point2f(0, 0),
        cv::Point2f(targetSize.width, 0),
        cv::Point2f(targetSize.width, targetSize.height),
        cv::Point2f(0, targetSize.height)
    };
    
    // 計算透視變換矩陣
    cv::Mat perspectiveMatrix = cv::getPerspectiveTransform(srcPoints, dstPoints);
    
    // 應用變換
    cv::Mat corrected;
    cv::warpPerspective(mat, corrected, perspectiveMatrix, 
                       cv::Size(targetSize.width, targetSize.height));
    
    // 轉換回CGImage
    UIImage *correctedUI = OCVMatToUIImageLocal(corrected);
    CGImageRef result = CGImageCreateCopy(correctedUI.CGImage);
    
    printf("透視變換: 已校正圖像至 %.0fx%.0f\n", targetSize.width, targetSize.height);
    return result;
#endif
}

// 色彩校正
+ (CGImageRef)applyColorCorrection:(CGImageRef)image
                      colorMatrix:(NSArray<NSArray<NSNumber*>*>*)matrix {
#if OCV_SIM_ARM64_STUB
    if (image) CFRetain(image);
    return image;
#else
    if (!image || matrix.count != 3) {
        CFRetain(image);
        return image;
    }
    
    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) {
        CFRetain(image);
        return image;
    }
    
    // 構建3x3變換矩陣
    cv::Mat colorMatrix = cv::Mat::eye(3, 3, CV_64F);
    for (int i = 0; i < 3; i++) {
        NSArray<NSNumber*> *row = matrix[i];
        if (row.count >= 3) {
            for (int j = 0; j < 3; j++) {
                colorMatrix.at<double>(i, j) = row[j].doubleValue;
            }
        }
    }
    
    // 轉換為浮點數並歸一化
    cv::Mat floatMat;
    mat.convertTo(floatMat, CV_32F, 1.0/255.0);
    
    // 重塑為N×3矩陣（每行一個像素的RGB值）
    int rows = floatMat.rows;
    int cols = floatMat.cols;
    cv::Mat reshaped = floatMat.reshape(1, rows * cols).t(); // 3×(N)
    
    // 應用色彩變換
    cv::Mat colorCorrected;
    cv::Mat colorMatrix32F;
    colorMatrix.convertTo(colorMatrix32F, CV_32F);
    colorCorrected = colorMatrix32F * reshaped;
    
    // 重塑回原始形狀
    cv::Mat transposed = colorCorrected.t();
    colorCorrected = transposed.reshape(3, rows);
    
    // 轉回8位並約束範圍
    cv::Mat result;
    colorCorrected.convertTo(result, CV_8U, 255.0);
    cv::cvtColor(result, result, cv::COLOR_RGB2BGR); // 確保色彩順序正確
    
    UIImage *correctedUI = OCVMatToUIImageLocal(result);
    CGImageRef finalResult = CGImageCreateCopy(correctedUI.CGImage);
    
    printf("色彩校正: 已應用3x3色彩變換矩陣\n");
    return finalResult;
#endif
}

@end

// =============================
// OpenCVContourAnalyzer 實作
// =============================

@implementation OpenCVContourAnalyzer

+ (NSDictionary<NSString*, NSNumber*> *)analyzeMaskCGImage:(CGImageRef)image
                                                maxContours:(NSUInteger)maxContours
                                            simplifyEpsilon:(double)epsilon {
#if OCV_SIM_ARM64_STUB
    // 模擬器 arm64 stub：避免使用 OpenCV，回傳零統計
    return @{ @"totalArea": @(0.0), @"totalPerimeter": @(0.0), @"contourCount": @(0) };
#else
    if (!image) {
        return @{ @"totalArea": @(0.0), @"totalPerimeter": @(0.0), @"contourCount": @(0) };
    }

    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    OCVUIImageToMatLocal(ui, mat);
    if (mat.empty()) {
        return @{ @"totalArea": @(0.0), @"totalPerimeter": @(0.0), @"contourCount": @(0) };
    }

    // 轉灰階、二值化（白為前景）
    cv::Mat gray, binary;
    if (mat.channels() == 3) {
        cv::cvtColor(mat, gray, cv::COLOR_BGR2GRAY);
    } else if (mat.channels() == 4) {
        cv::cvtColor(mat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = mat;
    }
    cv::threshold(gray, binary, 128, 255, cv::THRESH_BINARY);

    // 找輪廓（含階層）
    std::vector<std::vector<cv::Point>> contours;
    std::vector<cv::Vec4i> hierarchy;
    cv::findContours(binary, contours, hierarchy, cv::RETR_CCOMP, cv::CHAIN_APPROX_SIMPLE);

    // 根據面積排序、限制處理數量
    std::vector<std::pair<double, size_t>> areas;
    areas.reserve(contours.size());
    for (size_t i = 0; i < contours.size(); ++i) {
        areas.emplace_back(cv::contourArea(contours[i]), i);
    }
    std::sort(areas.begin(), areas.end(), [](const auto &a, const auto &b){ return a.first > b.first; });
    if (maxContours > 0 && areas.size() > maxContours) {
        areas.resize(maxContours);
    }

    double totalArea = 0.0;
    double totalPerimeter = 0.0;
    int used = 0;

    for (const auto &p : areas) {
        size_t idx = p.second;
        std::vector<cv::Point> approx;
        if (epsilon > 0.0) {
            cv::approxPolyDP(contours[idx], approx, epsilon, true);
        } else {
            approx = contours[idx];
        }

        double a = cv::contourArea(approx, false);
        double per = cv::arcLength(approx, true);

        // 洞扣除：有 parent 則視為洞，從總面積扣除
        int parent = hierarchy.empty() ? -1 : hierarchy[(int)idx][3];
        if (parent >= 0) {
            totalArea -= a;
        } else {
            totalArea += a;
        }
        totalPerimeter += per;
        used += 1;
    }

    return @{ @"totalArea": @(totalArea), @"totalPerimeter": @(totalPerimeter), @"contourCount": @(used) };
#endif
}

@end

