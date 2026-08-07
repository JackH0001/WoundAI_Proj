#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

// MARK: - 通用檢測結果結構體

@interface OpenCVDetectionResult : NSObject
@property (nonatomic, assign) CGRect boundingBox;
@property (nonatomic, assign) CGPoint center;
@property (nonatomic, assign) double confidence;
@property (nonatomic, assign) double area;
@property (nonatomic, assign) double perimeter;
@property (nonatomic, strong) NSArray<NSValue *> *contourPoints;
@end

@interface OpenCVCircleResult : OpenCVDetectionResult
@property (nonatomic, assign) double radius;
@property (nonatomic, assign) double diameter;
@property (nonatomic, assign) double circularity;
@end

@interface OpenCVSquareResult : OpenCVDetectionResult  
@property (nonatomic, assign) double aspectRatio;
@property (nonatomic, assign) double angleRotation;
@property (nonatomic, strong) NSArray<NSValue *> *cornerPoints;
@end

@interface OpenCVColorPointResult : OpenCVDetectionResult
@property (nonatomic, strong) NSString *colorName;
@property (nonatomic, strong) NSArray<NSNumber *> *hsvValues;
@property (nonatomic, strong) NSArray<NSNumber *> *rgbValues;
@end

// MARK: - 統一OpenCV包裝器 - 支援所有平台和功能

@interface OpenCVUniversalWrapper : NSObject

// MARK: - 圓形檢測 (校正貼紙)
+ (NSArray<OpenCVCircleResult *> *)detectCirclesAdvanced:(UIImage *)image
                                               minRadius:(int)minRadius
                                               maxRadius:(int)maxRadius
                                              parameters:(NSDictionary *)params
    NS_SWIFT_NAME(detectCirclesAdvanced(image:minRadius:maxRadius:parameters:));

// MARK: - 方形/矩形檢測 (校正貼紙外框)
+ (NSArray<OpenCVSquareResult *> *)detectSquaresAdvanced:(UIImage *)image
                                                 minSize:(double)minSize
                                                 maxSize:(double)maxSize
                                              parameters:(NSDictionary *)params
    NS_SWIFT_NAME(detectSquaresAdvanced(image:minSize:maxSize:parameters:));

// MARK: - RGBY色彩點檢測
+ (NSArray<OpenCVColorPointResult *> *)detectColorPoints:(UIImage *)image
                                                inRegion:(CGRect)region
                                              colorSpecs:(NSArray<NSDictionary *> *)colorSpecs
    NS_SWIFT_NAME(detectColorPoints(image:inRegion:colorSpecs:));

// MARK: - 輪廓分析和測量
+ (NSArray<OpenCVDetectionResult *> *)analyzeContours:(UIImage *)image
                                            maskImage:(UIImage * _Nullable)mask
                                           parameters:(NSDictionary *)params
    NS_SWIFT_NAME(analyzeContours(image:maskImage:parameters:));

// MARK: - 透視校正
+ (UIImage *)correctPerspective:(UIImage *)image
                   cornerPoints:(NSArray<NSValue *> *)corners
                     targetSize:(CGSize)targetSize
    NS_SWIFT_NAME(correctPerspective(image:cornerPoints:targetSize:));

// MARK: - 色彩校正
+ (UIImage *)correctColor:(UIImage *)image
             colorMatrix:(NSArray<NSArray<NSNumber *> *> *)matrix
    NS_SWIFT_NAME(correctColor(image:colorMatrix:));

// MARK: - 影像品質分析
+ (NSDictionary *)analyzeImageQuality:(UIImage *)image
    NS_SWIFT_NAME(analyzeImageQuality(image:));

// MARK: - 傷口特徵分析
+ (NSDictionary *)analyzeWoundFeatures:(UIImage *)image
                              roiRegion:(CGRect)roi
                            parameters:(NSDictionary *)params
    NS_SWIFT_NAME(analyzeWoundFeatures(image:roiRegion:parameters:));

// MARK: - 邊緣和紋理檢測
+ (UIImage *)detectEdges:(UIImage *)image
              parameters:(NSDictionary *)params
    NS_SWIFT_NAME(detectEdges(image:parameters:));

+ (NSDictionary *)analyzeTexture:(UIImage *)image
                        roiRegion:(CGRect)roi
    NS_SWIFT_NAME(analyzeTexture(image:roiRegion:));

// MARK: - 模板匹配 (用於校正貼紙識別)
+ (NSArray<OpenCVDetectionResult *> *)matchTemplate:(UIImage *)image
                                           template:(UIImage *)templateImage
                                          threshold:(double)threshold
    NS_SWIFT_NAME(matchTemplate(image:template:threshold:));

// MARK: - 影像預處理工具
+ (UIImage *)preprocessImage:(UIImage *)image
                  operations:(NSArray<NSString *> *)operations
                  parameters:(NSDictionary *)params
    NS_SWIFT_NAME(preprocessImage(image:operations:parameters:));

// MARK: - 幾何變換
+ (UIImage *)applyTransform:(UIImage *)image
                  transform:(CGAffineTransform)transform
                 outputSize:(CGSize)outputSize
    NS_SWIFT_NAME(applyTransform(image:transform:outputSize:));

// MARK: - 統計和測量
+ (NSDictionary *)calculateImageStatistics:(UIImage *)image
                                    region:(CGRect)region
    NS_SWIFT_NAME(calculateImageStatistics(image:region:));

// MARK: - 平台檢測
+ (BOOL)isSimulator NS_SWIFT_NAME(isSimulator());
+ (NSString *)getCurrentPlatform NS_SWIFT_NAME(getCurrentPlatform());

// MARK: - 版本和功能檢測
+ (NSString *)getOpenCVVersion NS_SWIFT_NAME(getOpenCVVersion());
+ (NSDictionary *)getAvailableFeatures NS_SWIFT_NAME(getAvailableFeatures());

@end

// 額外的輪廓統計分析器（供 Swift 直接呼叫）
@interface OpenCVContourAnalyzer : NSObject
// 回傳總面積、總周長、使用輪廓數量等彙總統計
+ (NSDictionary<NSString*, NSNumber*> *)analyzeMaskCGImage:(CGImageRef)image
                                               maxContours:(NSUInteger)maxContours
                                           simplifyEpsilon:(double)epsilon
    NS_SWIFT_NAME(analyzeMaskCGImage(_:maxContours:simplifyEpsilon:));
@end

NS_ASSUME_NONNULL_END