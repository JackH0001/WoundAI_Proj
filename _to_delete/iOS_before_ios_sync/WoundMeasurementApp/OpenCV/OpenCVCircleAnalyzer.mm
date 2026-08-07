//
//  OpenCVContourAnalyzer.mm
//
#import <UIKit/UIKit.h>
#import <opencv2/imgproc.hpp>
#import <opencv2/imgcodecs/ios.h>
#import "OpenCVCircleDetector.h"

using namespace cv;

@implementation OpenCVContourAnalyzer

+ (NSDictionary<NSString*, NSNumber*> *)analyzeMaskCGImage:(CGImageRef)image
                                                maxContours:(NSUInteger)maxContours
                                            simplifyEpsilon:(double)epsilon {
    if (!image) {
        return @{ "totalArea": @(0.0), "totalPerimeter": @(0.0), "contourCount": @(0) };
    }

    UIImage *ui = [UIImage imageWithCGImage:image];
    cv::Mat mat;
    UIImageToMat(ui, mat);
    if (mat.empty()) {
        return @{ "totalArea": @(0.0), "totalPerimeter": @(0.0), "contourCount": @(0) };
    }

    // 轉灰階、二值化（假設白為前景）
    cv::Mat gray, binary;
    if (mat.channels() == 3) {
        cv::cvtColor(mat, gray, cv::COLOR_BGR2GRAY);
    } else if (mat.channels() == 4) {
        cv::cvtColor(mat, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = mat;
    }
    cv::threshold(gray, binary, 128, 255, cv::THRESH_BINARY);

    // 找輪廓（含階層，支援洞）
    std::vector<std::vector<cv::Point>> contours;
    std::vector<cv::Vec4i> hierarchy;
    cv::findContours(binary, contours, hierarchy, cv::RETR_CCOMP, cv::CHAIN_APPROX_SIMPLE);

    // 根據大小排序、限制最多處理數量
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

        // 洞處理：如果 hierarchy 指出其 parent 存在，則從總面積扣除（簡化）
        int parent = hierarchy.empty() ? -1 : hierarchy[(int)idx][3];
        if (parent >= 0) {
            totalArea -= a;
        } else {
            totalArea += a;
        }
        totalPerimeter += per;
        used += 1;
    }

    return @{ "totalArea": @(totalArea), "totalPerimeter": @(totalPerimeter), "contourCount": @(used) };
}

@end

