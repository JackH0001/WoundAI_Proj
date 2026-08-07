#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>

NS_ASSUME_NONNULL_BEGIN

@interface OpenCVCircleDetector : NSObject
// 新版 API：提供 Swift 友善的命名（in:）
+ (NSArray<NSValue*> *)detectCircles:(CGImageRef)image
                                   minRadius:(int)minRadius
                                   maxRadius:(int)maxRadius
                                     dpRatio:(double)dp
                              minDistBetween:(double)minDist
                               cannyThreshold:(double)cannyThresh
                             accumulatorThreshold:(double)accThresh
                                         topN:(NSUInteger)topN
    NS_SWIFT_NAME(detectCircles(in:minRadius:maxRadius:dpRatio:minDistBetween:cannyThreshold:accumulatorThreshold:topN:));

// 多通道增強檢測 API：支援不同色彩空間和參數掃描
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
                                    parameterSweep:(BOOL)enableSweep
    NS_SWIFT_NAME(detectCirclesMultiChannel(in:minRadius:maxRadius:dpRatio:minDistBetween:cannyThreshold:accumulatorThreshold:topN:useLabChannel:useHSVChannel:useRGBChannels:parameterSweep:));

// 舊版相容 API（指定 Swift 名稱避免與新版混淆）
+ (NSArray<NSValue*> *)detectCirclesInCGImage:(CGImageRef)image
                                   minRadius:(int)minRadius
                                   maxRadius:(int)maxRadius
                                     dpRatio:(double)dp
                              minDistBetween:(double)minDist
                               cannyThreshold:(double)cannyThresh
                             accumulatorThreshold:(double)accThresh
                                         topN:(NSUInteger)topN
    NS_SWIFT_NAME(detectCirclesInCGImage(_:minRadius:maxRadius:dpRatio:minDistBetween:cannyThreshold:accumulatorThreshold:topN:));
@end

// OpenCV 輪廓分析：以 findContours 計算二值遮罩的幾何面積與周長
@interface OpenCVContourAnalyzer : NSObject
/// 傳入二值遮罩 CGImage（白=前景），回傳總面積與周長（像素空間）。
/// - Parameters:
///   - image: 二值遮罩 CGImage（RGBA/灰階皆可）
///   - maxContours: 最大處理的輪廓數量上限（避免極端耗時）
///   - epsilon: 近似精度（approxPolyDP epsilon，像素單位）
/// - Returns: NSDictionary 包含 keys: totalArea(Double), totalPerimeter(Double), contourCount(Int)
+ (NSDictionary<NSString*, NSNumber*> *)analyzeMaskCGImage:(CGImageRef)image
                                                maxContours:(NSUInteger)maxContours
                                            simplifyEpsilon:(double)epsilon;
@end

// 方形校正貼紙檢測器
@interface OpenCVSquareDetector : NSObject
// 檢測方形邊界
+ (NSArray<NSValue*> *)detectSquares:(CGImageRef)image
                          minSize:(double)minSize
                          maxSize:(double)maxSize
                       aspectRatio:(double)targetRatio
                         tolerance:(double)tolerance
                              topN:(NSUInteger)topN
    NS_SWIFT_NAME(detectSquares(in:minSize:maxSize:aspectRatio:tolerance:topN:));

// 檢測四角凸點
+ (NSArray<NSValue*> *)detectCornerDots:(CGImageRef)image
                                inRegion:(CGRect)region
                               minRadius:(int)minRadius
                               maxRadius:(int)maxRadius
                                    topN:(NSUInteger)topN
    NS_SWIFT_NAME(detectCornerDots(in:region:minRadius:maxRadius:topN:));

// RGBY色彩點檢測
+ (NSArray<NSDictionary*> *)detectColorPoints:(CGImageRef)image
                                     inRegion:(CGRect)region
                                   colorNames:(NSArray<NSString*>*)colorNames
                                  hsvRanges:(NSArray<NSArray<NSNumber*>*>*)ranges
    NS_SWIFT_NAME(detectColorPoints(in:region:colorNames:hsvRanges:));

// 透視變換校正
+ (CGImageRef)applyPerspectiveCorrection:(CGImageRef)image
                             cornerPoints:(NSArray<NSValue*>*)corners
                               targetSize:(CGSize)targetSize
    NS_SWIFT_NAME(applyPerspectiveCorrection(to:cornerPoints:targetSize:)) CF_RETURNS_RETAINED;

// 色彩校正
+ (CGImageRef)applyColorCorrection:(CGImageRef)image
                      colorMatrix:(NSArray<NSArray<NSNumber*>*>*)matrix
    NS_SWIFT_NAME(applyColorCorrection(to:colorMatrix:)) CF_RETURNS_RETAINED;
@end

NS_ASSUME_NONNULL_END

