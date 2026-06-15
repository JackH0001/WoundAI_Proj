#import "OpenCVUniversalWrapper.h"
#import <opencv2/opencv.hpp>
#import <opencv2/imgproc.hpp>
#import <opencv2/imgcodecs.hpp>
#import <opencv2/features2d.hpp>

using namespace cv;
using namespace std;

// MARK: - 結果類別實作

@implementation OpenCVDetectionResult
@end

@implementation OpenCVCircleResult
@end

@implementation OpenCVSquareResult
@end

@implementation OpenCVColorPointResult
@end

// MARK: - C++ 輔助函數

namespace OpenCVHelpers {
    
    // UIImage 轉 cv::Mat
    cv::Mat UIImageToMat(UIImage *image) {
        CGColorSpaceRef colorSpace = CGImageGetColorSpace(image.CGImage);
        CGFloat cols = image.size.width;
        CGFloat rows = image.size.height;
        
        cv::Mat cvMat(rows, cols, CV_8UC4); // 8 bits per component, 4 channels (ARGB)
        
        CGContextRef contextRef = CGBitmapContextCreate(cvMat.data,
                                                       cols,
                                                       rows,
                                                       8,
                                                       cvMat.step[0],
                                                       colorSpace,
                                                       kCGImageAlphaNoneSkipLast | kCGBitmapByteOrderDefault);
        
        CGContextDrawImage(contextRef, CGRectMake(0, 0, cols, rows), image.CGImage);
        CGContextRelease(contextRef);
        
        cv::Mat cvMatRGB;
        cv::cvtColor(cvMat, cvMatRGB, cv::COLOR_RGBA2RGB);
        
        return cvMatRGB;
    }
    
    // cv::Mat 轉 UIImage
    UIImage* MatToUIImage(const cv::Mat& cvMat) {
        NSData *data = [NSData dataWithBytes:cvMat.data length:cvMat.elemSize() * cvMat.total()];
        
        CGColorSpaceRef colorSpace;
        
        if (cvMat.elemSize() == 1) {
            colorSpace = CGColorSpaceCreateDeviceGray();
        } else {
            colorSpace = CGColorSpaceCreateDeviceRGB();
        }
        
        CGDataProviderRef provider = CGDataProviderCreateWithCFData((__bridge CFDataRef)data);
        
        CGImageRef imageRef = CGImageCreate(cvMat.cols,
                                          cvMat.rows,
                                          8,
                                          8 * cvMat.elemSize(),
                                          cvMat.step[0],
                                          colorSpace,
                                          kCGImageAlphaNone | kCGBitmapByteOrderDefault,
                                          provider,
                                          NULL,
                                          false,
                                          kCGRenderingIntentDefault);
        
        UIImage *finalImage = [UIImage imageWithCGImage:imageRef];
        
        CGImageRelease(imageRef);
        CGDataProviderRelease(provider);
        CGColorSpaceRelease(colorSpace);
        
        return finalImage;
    }
    
    // 檢測平台
    bool isRunningOnSimulator() {
        #if TARGET_OS_SIMULATOR
            return true;
        #else
            return false;
        #endif
    }
    
    // HSV 色彩範圍檢查
    bool isColorInRange(const cv::Vec3b& hsv, const cv::Scalar& lower, const cv::Scalar& upper) {
        return hsv[0] >= lower[0] && hsv[0] <= upper[0] &&
               hsv[1] >= lower[1] && hsv[1] <= upper[1] &&
               hsv[2] >= lower[2] && hsv[2] <= upper[2];
    }
    
    // 圓形度計算
    double calculateCircularity(const std::vector<cv::Point>& contour) {
        double area = cv::contourArea(contour);
        double perimeter = cv::arcLength(contour, true);
        return (4 * CV_PI * area) / (perimeter * perimeter);
    }
    
    // 方形度計算
    double calculateSquareness(const std::vector<cv::Point>& contour) {
        cv::RotatedRect minRect = cv::minAreaRect(contour);
        double area = cv::contourArea(contour);
        double rectArea = minRect.size.width * minRect.size.height;
        return area / rectArea;
    }
}

// MARK: - 主要實作

@implementation OpenCVUniversalWrapper

// MARK: - 進階圓形檢測
+ (NSArray<OpenCVCircleResult *> *)detectCirclesAdvanced:(UIImage *)image
                                               minRadius:(int)minRadius
                                               maxRadius:(int)maxRadius
                                              parameters:(NSDictionary *)params {
    
    if (!image) return @[];
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat gray;
    cv::cvtColor(src, gray, cv::COLOR_RGB2GRAY);
    
    // 提取參數
    double dp = [params[@"dp"] doubleValue] ?: 1.0;
    double minDist = [params[@"minDist"] doubleValue] ?: gray.rows / 8;
    double param1 = [params[@"param1"] doubleValue] ?: 100;
    double param2 = [params[@"param2"] doubleValue] ?: 30;
    int maxCircles = [params[@"maxCircles"] intValue] ?: 10;
    
    std::vector<cv::Vec3f> circles;
    cv::HoughCircles(gray, circles, cv::HOUGH_GRADIENT, dp, minDist,
                    param1, param2, minRadius, maxRadius);
    
    NSMutableArray<OpenCVCircleResult *> *results = [NSMutableArray array];
    
    // 限制結果數量並按半徑排序
    std::sort(circles.begin(), circles.end(), 
             [](const cv::Vec3f& a, const cv::Vec3f& b) {
                 return a[2] > b[2]; // 按半徑降序
             });
    
    for (size_t i = 0; i < std::min((size_t)maxCircles, circles.size()); i++) {
        cv::Vec3f c = circles[i];
        
        OpenCVCircleResult *result = [[OpenCVCircleResult alloc] init];
        result.center = CGPointMake(c[0], c[1]);
        result.radius = c[2];
        result.diameter = c[2] * 2;
        
        // 計算邊界框
        result.boundingBox = CGRectMake(c[0] - c[2], c[1] - c[2], c[2] * 2, c[2] * 2);
        result.area = M_PI * c[2] * c[2];
        result.perimeter = 2 * M_PI * c[2];
        
        // 計算圓形度（實際輪廓與理想圓的匹配度）
        result.circularity = 1.0; // 預設為完美圓形
        
        // 基於Hough變換的置信度估算
        result.confidence = std::max(0.0, std::min(1.0, (param2 - 20) / (param1 - 20)));
        
        [results addObject:result];
    }
    
    return [results copy];
}

// MARK: - 進階方形檢測  
+ (NSArray<OpenCVSquareResult *> *)detectSquaresAdvanced:(UIImage *)image
                                                 minSize:(double)minSize
                                                 maxSize:(double)maxSize
                                              parameters:(NSDictionary *)params {
    
    if (!image) return @[];
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat gray, binary;
    cv::cvtColor(src, gray, cv::COLOR_RGB2GRAY);
    
    // 提取參數
    double thresholdValue = [params[@"threshold"] doubleValue] ?: 127;
    double minArea = minSize * minSize;
    double maxArea = maxSize * maxSize;
    double aspectRatioTolerance = [params[@"aspectRatioTolerance"] doubleValue] ?: 0.3;
    
    cv::threshold(gray, binary, thresholdValue, 255, cv::THRESH_BINARY);
    
    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(binary, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
    
    NSMutableArray<OpenCVSquareResult *> *results = [NSMutableArray array];
    
    for (const auto& contour : contours) {
        double area = cv::contourArea(contour);
        if (area < minArea || area > maxArea) continue;
        
        // 多邊形逼近
        std::vector<cv::Point> approx;
        double epsilon = 0.02 * cv::arcLength(contour, true);
        cv::approxPolyDP(contour, approx, epsilon, true);
        
        // 檢查是否為4邊形
        if (approx.size() != 4) continue;
        
        // 檢查是否為凸多邊形
        if (!cv::isContourConvex(approx)) continue;
        
        // 計算長寬比
        cv::RotatedRect minRect = cv::minAreaRect(contour);
        double aspectRatio = std::max(minRect.size.width, minRect.size.height) / 
                            std::min(minRect.size.width, minRect.size.height);
        
        // 檢查長寬比是否接近正方形
        if (std::abs(aspectRatio - 1.0) > aspectRatioTolerance) continue;
        
        OpenCVSquareResult *result = [[OpenCVSquareResult alloc] init];
        
        // 計算邊界框
        cv::Rect boundingRect = cv::boundingRect(contour);
        result.boundingBox = CGRectMake(boundingRect.x, boundingRect.y, 
                                       boundingRect.width, boundingRect.height);
        
        // 計算中心點
        cv::Moments moments = cv::moments(contour);
        result.center = CGPointMake(moments.m10 / moments.m00, moments.m01 / moments.m00);
        
        result.area = area;
        result.perimeter = cv::arcLength(contour, true);
        result.aspectRatio = aspectRatio;
        result.angleRotation = minRect.angle;
        
        // 轉換角點座標
        NSMutableArray *cornerPoints = [NSMutableArray array];
        for (const auto& point : approx) {
            [cornerPoints addObject:[NSValue valueWithCGPoint:CGPointMake(point.x, point.y)]];
        }
        result.cornerPoints = [cornerPoints copy];
        
        // 計算方形度作為置信度
        result.confidence = OpenCVHelpers::calculateSquareness(contour);
        
        [results addObject:result];
    }
    
    // 按面積排序
    [results sortUsingComparator:^NSComparisonResult(OpenCVSquareResult *a, OpenCVSquareResult *b) {
        return a.area > b.area ? NSOrderedAscending : NSOrderedDescending;
    }];
    
    return [results copy];
}

// MARK: - RGBY 色彩點檢測
+ (NSArray<OpenCVColorPointResult *> *)detectColorPoints:(UIImage *)image
                                                inRegion:(CGRect)region
                                              colorSpecs:(NSArray<NSDictionary *> *)colorSpecs {
    
    if (!image || colorSpecs.count == 0) return @[];
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat hsv;
    cv::cvtColor(src, hsv, cv::COLOR_RGB2HSV);
    
    // 裁切感興趣區域
    cv::Rect roi(region.origin.x, region.origin.y, region.size.width, region.size.height);
    roi &= cv::Rect(0, 0, src.cols, src.rows); // 確保ROI在圖像範圍內
    
    cv::Mat regionHSV = hsv(roi);
    cv::Mat regionRGB = src(roi);
    
    NSMutableArray<OpenCVColorPointResult *> *results = [NSMutableArray array];
    
    for (NSDictionary *colorSpec in colorSpecs) {
        NSString *colorName = colorSpec[@"name"];
        NSArray *lowerBound = colorSpec[@"lower"]; // [H, S, V]
        NSArray *upperBound = colorSpec[@"upper"];
        
        if (!colorName || !lowerBound || !upperBound) continue;
        
        cv::Scalar lower(
            [lowerBound[0] doubleValue],
            [lowerBound[1] doubleValue],
            [lowerBound[2] doubleValue]
        );
        cv::Scalar upper(
            [upperBound[0] doubleValue],
            [upperBound[1] doubleValue], 
            [upperBound[2] doubleValue]
        );
        
        cv::Mat mask;
        cv::inRange(regionHSV, lower, upper, mask);
        
        // 形態學操作清理噪點
        cv::Mat kernel = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(5, 5));
        cv::morphologyEx(mask, mask, cv::MORPH_OPEN, kernel);
        cv::morphologyEx(mask, mask, cv::MORPH_CLOSE, kernel);
        
        // 尋找輪廓
        std::vector<std::vector<cv::Point>> contours;
        cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
        
        for (const auto& contour : contours) {
            double area = cv::contourArea(contour);
            if (area < 100) continue; // 過濾小區域
            
            // 計算重心
            cv::Moments moments = cv::moments(contour);
            cv::Point2f center(moments.m10 / moments.m00, moments.m01 / moments.m00);
            
            // 轉換回原始座標系
            cv::Point2f globalCenter = center + cv::Point2f(roi.x, roi.y);
            
            // 提取該點的RGB和HSV值
            cv::Vec3b rgbValue = regionRGB.at<cv::Vec3b>((int)center.y, (int)center.x);
            cv::Vec3b hsvValue = regionHSV.at<cv::Vec3b>((int)center.y, (int)center.x);
            
            OpenCVColorPointResult *result = [[OpenCVColorPointResult alloc] init];
            result.center = CGPointMake(globalCenter.x, globalCenter.y);
            result.area = area;
            result.colorName = colorName;
            
            result.rgbValues = @[
                @(rgbValue[2]), // R (OpenCV uses BGR)
                @(rgbValue[1]), // G
                @(rgbValue[0])  // B
            ];
            
            result.hsvValues = @[
                @(hsvValue[0]), // H
                @(hsvValue[1]), // S
                @(hsvValue[2])  // V
            ];
            
            // 計算邊界框
            cv::Rect boundingRect = cv::boundingRect(contour);
            result.boundingBox = CGRectMake(
                boundingRect.x + roi.x,
                boundingRect.y + roi.y,
                boundingRect.width,
                boundingRect.height
            );
            
            // 基於面積和形狀計算置信度
            double circularity = OpenCVHelpers::calculateCircularity(contour);
            result.confidence = std::min(1.0, (area / 1000.0) * circularity);
            
            [results addObject:result];
        }
    }
    
    // 按置信度排序
    [results sortUsingComparator:^NSComparisonResult(OpenCVColorPointResult *a, OpenCVColorPointResult *b) {
        return a.confidence > b.confidence ? NSOrderedAscending : NSOrderedDescending;
    }];
    
    return [results copy];
}

// MARK: - 輪廓分析
+ (NSArray<OpenCVDetectionResult *> *)analyzeContours:(UIImage *)image
                                            maskImage:(UIImage * _Nullable)mask
                                           parameters:(NSDictionary *)params {
    
    if (!image) return @[];
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat processImage;
    
    if (mask) {
        cv::Mat maskMat = OpenCVHelpers::UIImageToMat(mask);
        cv::cvtColor(maskMat, processImage, cv::COLOR_RGB2GRAY);
    } else {
        cv::cvtColor(src, processImage, cv::COLOR_RGB2GRAY);
        cv::threshold(processImage, processImage, 127, 255, cv::THRESH_BINARY);
    }
    
    double minArea = [params[@"minArea"] doubleValue] ?: 100;
    double maxArea = [params[@"maxArea"] doubleValue] ?: src.rows * src.cols * 0.5;
    
    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(processImage, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
    
    NSMutableArray<OpenCVDetectionResult *> *results = [NSMutableArray array];
    
    for (const auto& contour : contours) {
        double area = cv::contourArea(contour);
        if (area < minArea || area > maxArea) continue;
        
        OpenCVDetectionResult *result = [[OpenCVDetectionResult alloc] init];
        
        // 計算基本屬性
        cv::Moments moments = cv::moments(contour);
        result.center = CGPointMake(moments.m10 / moments.m00, moments.m01 / moments.m00);
        result.area = area;
        result.perimeter = cv::arcLength(contour, true);
        
        // 計算邊界框
        cv::Rect boundingRect = cv::boundingRect(contour);
        result.boundingBox = CGRectMake(boundingRect.x, boundingRect.y, 
                                       boundingRect.width, boundingRect.height);
        
        // 轉換輪廓點
        NSMutableArray *contourPoints = [NSMutableArray array];
        for (const auto& point : contour) {
            [contourPoints addObject:[NSValue valueWithCGPoint:CGPointMake(point.x, point.y)]];
        }
        result.contourPoints = [contourPoints copy];
        
        // 計算置信度（基於形狀規整度）
        double circularity = OpenCVHelpers::calculateCircularity(contour);
        result.confidence = std::min(1.0, circularity * 2.0);
        
        [results addObject:result];
    }
    
    // 按面積排序
    [results sortUsingComparator:^NSComparisonResult(OpenCVDetectionResult *a, OpenCVDetectionResult *b) {
        return a.area > b.area ? NSOrderedAscending : NSOrderedDescending;
    }];
    
    return [results copy];
}

// MARK: - 透視校正
+ (UIImage *)correctPerspective:(UIImage *)image
                   cornerPoints:(NSArray<NSValue *> *)corners
                     targetSize:(CGSize)targetSize {
    
    if (!image || corners.count != 4) return image;
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    
    // 設定源點
    std::vector<cv::Point2f> srcPoints;
    for (NSValue *cornerValue in corners) {
        CGPoint point = [cornerValue CGPointValue];
        srcPoints.push_back(cv::Point2f(point.x, point.y));
    }
    
    // 設定目標點
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
    cv::warpPerspective(src, corrected, perspectiveMatrix, 
                       cv::Size(targetSize.width, targetSize.height));
    
    return OpenCVHelpers::MatToUIImage(corrected);
}

// MARK: - 色彩校正
+ (UIImage *)correctColor:(UIImage *)image
             colorMatrix:(NSArray<NSArray<NSNumber *> *> *)matrix {
    
    if (!image || matrix.count != 3) return image;
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat corrected;
    src.copyTo(corrected);
    
    // 應用色彩矩陣
    for (int row = 0; row < corrected.rows; row++) {
        for (int col = 0; col < corrected.cols; col++) {
            cv::Vec3b& pixel = corrected.at<cv::Vec3b>(row, col);
            
            int newR = cv::saturate_cast<uchar>(
                pixel[2] * [matrix[0][0] doubleValue] +
                pixel[1] * [matrix[0][1] doubleValue] +
                pixel[0] * [matrix[0][2] doubleValue]
            );
            int newG = cv::saturate_cast<uchar>(
                pixel[2] * [matrix[1][0] doubleValue] +
                pixel[1] * [matrix[1][1] doubleValue] +
                pixel[0] * [matrix[1][2] doubleValue]
            );
            int newB = cv::saturate_cast<uchar>(
                pixel[2] * [matrix[2][0] doubleValue] +
                pixel[1] * [matrix[2][1] doubleValue] +
                pixel[0] * [matrix[2][2] doubleValue]
            );
            
            pixel[2] = newR; // R
            pixel[1] = newG; // G
            pixel[0] = newB; // B
        }
    }
    
    return OpenCVHelpers::MatToUIImage(corrected);
}

// MARK: - 影像品質分析
+ (NSDictionary *)analyzeImageQuality:(UIImage *)image {
    if (!image) return @{};
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat gray;
    cv::cvtColor(src, gray, cv::COLOR_RGB2GRAY);
    
    // 計算清晰度 (Laplacian variance)
    cv::Mat laplacian;
    cv::Laplacian(gray, laplacian, CV_64F);
    cv::Scalar meanLap, stdLap;
    cv::meanStdDev(laplacian, meanLap, stdLap);
    double sharpness = stdLap[0] * stdLap[0];
    
    // 計算亮度和對比度
    cv::Scalar meanBrightness, stdBrightness;
    cv::meanStdDev(gray, meanBrightness, stdBrightness);
    double brightness = meanBrightness[0];
    double contrast = stdBrightness[0];
    
    // 計算噪聲級別 (使用高頻分量)
    cv::Mat noise;
    cv::Mat kernel = (cv::Mat_<float>(3,3) << -1, -1, -1, -1, 8, -1, -1, -1, -1);
    cv::filter2D(gray, noise, -1, kernel);
    cv::Scalar noiseLevel = cv::mean(cv::abs(noise));
    
    return @{
        @"sharpness": @(sharpness),
        @"brightness": @(brightness),
        @"contrast": @(contrast),
        @"noiseLevel": @(noiseLevel[0]),
        @"resolution": @{
            @"width": @(src.cols),
            @"height": @(src.rows)
        }
    };
}

// MARK: - 傷口特徵分析
+ (NSDictionary *)analyzeWoundFeatures:(UIImage *)image
                              roiRegion:(CGRect)roi
                            parameters:(NSDictionary *)params {
    
    if (!image) return @{};
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Rect roiRect(roi.origin.x, roi.origin.y, roi.size.width, roi.size.height);
    roiRect &= cv::Rect(0, 0, src.cols, src.rows);
    
    cv::Mat roiImage = src(roiRect);
    cv::Mat hsvRoi, grayRoi;
    cv::cvtColor(roiImage, hsvRoi, cv::COLOR_RGB2HSV);
    cv::cvtColor(roiImage, grayRoi, cv::COLOR_RGB2GRAY);
    
    // 色彩分析
    cv::Scalar meanHSV = cv::mean(hsvRoi);
    cv::Scalar stdHSV;
    cv::meanStdDev(hsvRoi, cv::noArray(), stdHSV);
    
    // 紋理分析 (灰階共生矩陣的簡化版本)
    cv::Mat textureResponse;
    cv::Mat sobelX, sobelY;
    cv::Sobel(grayRoi, sobelX, CV_64F, 1, 0, 3);
    cv::Sobel(grayRoi, sobelY, CV_64F, 0, 1, 3);
    cv::magnitude(sobelX, sobelY, textureResponse);
    cv::Scalar textureVariance = cv::mean(textureResponse);
    
    // 邊緣粗糙度分析
    cv::Mat edges;
    cv::Canny(grayRoi, edges, 50, 150);
    int edgePixels = cv::countNonZero(edges);
    double edgeRoughness = (double)edgePixels / (roiImage.rows * roiImage.cols);
    
    return @{
        @"colorAnalysis": @{
            @"meanHue": @(meanHSV[0]),
            @"meanSaturation": @(meanHSV[1]),
            @"meanValue": @(meanHSV[2]),
            @"hueVariance": @(stdHSV[0]),
            @"saturationVariance": @(stdHSV[1]),
            @"valueVariance": @(stdHSV[2])
        },
        @"textureAnalysis": @{
            @"textureVariance": @(textureVariance[0]),
            @"edgeRoughness": @(edgeRoughness)
        },
        @"morphologyAnalysis": @{
            @"area": @(roi.size.width * roi.size.height),
            @"aspectRatio": @(roi.size.width / roi.size.height),
            @"edgePixels": @(edgePixels)
        }
    };
}

// MARK: - 工具方法
+ (UIImage *)detectEdges:(UIImage *)image parameters:(NSDictionary *)params {
    if (!image) return nil;
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Mat gray, edges;
    cv::cvtColor(src, gray, cv::COLOR_RGB2GRAY);
    
    double threshold1 = [params[@"threshold1"] doubleValue] ?: 50;
    double threshold2 = [params[@"threshold2"] doubleValue] ?: 150;
    int apertureSize = [params[@"apertureSize"] intValue] ?: 3;
    
    cv::Canny(gray, edges, threshold1, threshold2, apertureSize);
    
    return OpenCVHelpers::MatToUIImage(edges);
}

+ (NSDictionary *)analyzeTexture:(UIImage *)image roiRegion:(CGRect)roi {
    if (!image) return @{};
    
    cv::Mat src = OpenCVHelpers::UIImageToMat(image);
    cv::Rect roiRect(roi.origin.x, roi.origin.y, roi.size.width, roi.size.height);
    roiRect &= cv::Rect(0, 0, src.cols, src.rows);
    
    cv::Mat roiImage = src(roiRect);
    cv::Mat gray;
    cv::cvtColor(roiImage, gray, cv::COLOR_RGB2GRAY);
    
    // 計算局部二值模式 (LBP) 的簡化版本
    cv::Mat lbp = cv::Mat::zeros(gray.size(), CV_8UC1);
    for (int row = 1; row < gray.rows - 1; row++) {
        for (int col = 1; col < gray.cols - 1; col++) {
            uchar center = gray.at<uchar>(row, col);
            uchar code = 0;
            
            code |= (gray.at<uchar>(row - 1, col - 1) >= center) << 7;
            code |= (gray.at<uchar>(row - 1, col) >= center) << 6;
            code |= (gray.at<uchar>(row - 1, col + 1) >= center) << 5;
            code |= (gray.at<uchar>(row, col + 1) >= center) << 4;
            code |= (gray.at<uchar>(row + 1, col + 1) >= center) << 3;
            code |= (gray.at<uchar>(row + 1, col) >= center) << 2;
            code |= (gray.at<uchar>(row + 1, col - 1) >= center) << 1;
            code |= (gray.at<uchar>(row, col - 1) >= center) << 0;
            
            lbp.at<uchar>(row, col) = code;
        }
    }
    
    // 計算LBP統計
    cv::Scalar meanLBP, stdLBP;
    cv::meanStdDev(lbp, meanLBP, stdLBP);
    
    return @{
        @"lbpMean": @(meanLBP[0]),
        @"lbpStd": @(stdLBP[0]),
        @"textureHomogeneity": @(1.0 / (1.0 + stdLBP[0]))
    };
}

// MARK: - 平台檢測
+ (BOOL)isSimulator {
    return OpenCVHelpers::isRunningOnSimulator();
}

+ (NSString *)getCurrentPlatform {
    if (OpenCVHelpers::isRunningOnSimulator()) {
        return @"iOS Simulator";
    } else {
        return @"iOS Device";
    }
}

// MARK: - 版本信息
+ (NSString *)getOpenCVVersion {
    return [NSString stringWithCString:cv::getVersionString().c_str() encoding:NSUTF8StringEncoding];
}

+ (NSDictionary *)getAvailableFeatures {
    return @{
        @"version": [self getOpenCVVersion],
        @"platform": [self getCurrentPlatform],
        @"features": @[
            @"circleDetection",
            @"squareDetection", 
            @"colorPointDetection",
            @"contourAnalysis",
            @"perspectiveCorrection",
            @"colorCorrection",
            @"qualityAnalysis",
            @"woundFeatureAnalysis",
            @"edgeDetection",
            @"textureAnalysis"
        ]
    };
}

@end