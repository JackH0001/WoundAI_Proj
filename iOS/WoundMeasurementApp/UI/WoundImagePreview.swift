import SwiftUI
import UIKit

/// A read-only viewport: image and contour share one zooming view and coordinate system.
struct WoundZoomPreview: UIViewRepresentable {
    let image: UIImage
    let polygons: [[[Int]]]
    let imageW: Int
    let imageH: Int
    let resetToken: Int
    var markerPolygons: [[[Int]]] = []

    func makeUIView(context: Context) -> WoundZoomPreviewView { WoundZoomPreviewView() }
    func updateUIView(_ view: WoundZoomPreviewView, context: Context) {
        view.configure(image: image, polygons: polygons, imageW: imageW, imageH: imageH,
                       resetToken: resetToken, markerPolygons: markerPolygons)
    }
}

final class WoundZoomPreviewView: UIView, UIScrollViewDelegate {
    let scrollView = UIScrollView()
    let contentView = UIView()
    let imageView = UIImageView()
    let contourLayer = CAShapeLayer()
    let markerLayer = CAShapeLayer()
    private var markerPolygons: [[[Int]]] = []
    private var polygons: [[[Int]]] = []
    private var imageSize = CGSize.zero
    private var lastViewport = CGSize.zero
    private var lastResetToken = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        clipsToBounds = true
        scrollView.delegate = self
        scrollView.minimumZoomScale = 1
        scrollView.maximumZoomScale = 6
        scrollView.bouncesZoom = true
        scrollView.showsHorizontalScrollIndicator = false
        scrollView.showsVerticalScrollIndicator = false
        scrollView.contentInsetAdjustmentBehavior = .never
        // Single-finger swipes remain available to the containing results page.
        scrollView.panGestureRecognizer.minimumNumberOfTouches = 2
        scrollView.panGestureRecognizer.maximumNumberOfTouches = 2
        addSubview(scrollView)
        scrollView.addSubview(contentView)
        contentView.addSubview(imageView)
        imageView.contentMode = .scaleToFill
        contourLayer.fillColor = UIColor.clear.cgColor
        contourLayer.strokeColor = UIColor.cyan.cgColor
        contourLayer.lineJoin = .round
        contentView.layer.addSublayer(contourLayer)
        markerLayer.fillColor = UIColor.clear.cgColor
        markerLayer.strokeColor = UIColor.green.cgColor
        markerLayer.lineJoin = .round
        contentView.layer.addSublayer(markerLayer)
        isAccessibilityElement = true
        accessibilityLabel = "影像與圈選邊界預覽"
        accessibilityHint = "雙指放大、縮小與移動；不會修改圈選範圍。"
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func configure(image: UIImage, polygons: [[[Int]]], imageW: Int, imageH: Int, resetToken: Int, markerPolygons: [[[Int]]] = []) {
        let size = CGSize(width: max(imageW, 1), height: max(imageH, 1))
        let changedImage = imageView.image !== image || imageSize != size
        let changedPolygons = self.polygons != polygons || self.markerPolygons != markerPolygons
        imageView.image = image
        imageSize = size
        self.polygons = polygons
        self.markerPolygons = markerPolygons
        if changedImage || resetToken != lastResetToken {
            lastViewport = .zero
            setNeedsLayout()
        } else if changedPolygons {
            drawContour()
        }
        lastResetToken = resetToken
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        scrollView.frame = bounds
        guard bounds.width > 0, bounds.height > 0, imageSize.width > 0 else { return }
        guard lastViewport != bounds.size else { return }
        lastViewport = bounds.size
        scrollView.setZoomScale(1, animated: false)
        let fit = min(bounds.width / imageSize.width, bounds.height / imageSize.height)
        let size = CGSize(width: imageSize.width * fit, height: imageSize.height * fit)
        contentView.frame = CGRect(origin: .zero, size: size)
        imageView.frame = contentView.bounds
        scrollView.contentSize = size
        drawContour()
        centerContent()
        scrollView.contentOffset = CGPoint(x: -scrollView.contentInset.left, y: -scrollView.contentInset.top)
    }

    private func drawContour() {
        guard imageSize.width > 0, imageSize.height > 0 else { return }
        let sx = contentView.bounds.width / imageSize.width
        let sy = contentView.bounds.height / imageSize.height
        func path(for polygons: [[[Int]]]) -> CGPath {
            let path = UIBezierPath()
            for polygon in polygons where polygon.count >= 3 {
                guard polygon.allSatisfy({ $0.count == 2 }) else { continue }
                path.move(to: CGPoint(x: CGFloat(polygon[0][0]) * sx, y: CGFloat(polygon[0][1]) * sy))
                for point in polygon.dropFirst() {
                    path.addLine(to: CGPoint(x: CGFloat(point[0]) * sx, y: CGFloat(point[1]) * sy))
                }
                path.close()
            }
            return path.cgPath
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        contourLayer.frame = contentView.bounds
        contourLayer.path = path(for: polygons)
        markerLayer.frame = contentView.bounds
        markerLayer.path = path(for: markerPolygons)
        markerLayer.lineWidth = 3 / max(scrollView.zoomScale, 1)
        contourLayer.lineWidth = 2 / max(scrollView.zoomScale, 1)
        CATransaction.commit()
    }

    private func centerContent() {
        let x = max(0, (scrollView.bounds.width - contentView.frame.width) / 2)
        let y = max(0, (scrollView.bounds.height - contentView.frame.height) / 2)
        scrollView.contentInset = UIEdgeInsets(top: y, left: x, bottom: y, right: x)
    }
    func viewForZooming(in scrollView: UIScrollView) -> UIView? { contentView }
    func scrollViewDidZoom(_ scrollView: UIScrollView) {
        centerContent()
        markerLayer.lineWidth = 3 / max(scrollView.zoomScale, 1)
        contourLayer.lineWidth = 2 / max(scrollView.zoomScale, 1)
    }
}

/// Shared read-only inspection controls; zooming never changes the saved contour.
struct WoundImagePreview: View {
    let image: UIImage
    let polygons: [[[Int]]]
    let imageW: Int
    let imageH: Int
    var markerPolygons: [[[Int]]] = []
    var height: CGFloat = 300
    @State private var resetToken = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            WoundZoomPreview(image: image, polygons: polygons, imageW: imageW, imageH: imageH,
                             resetToken: resetToken, markerPolygons: markerPolygons)
                .frame(height: height)
                .background(Color.black.opacity(0.05))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            HStack {
                Text("雙指縮放與移動，檢查圈選邊界。")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 4)
                Button("重設檢視") { resetToken += 1 }
                    .font(.caption).frame(minHeight: 44)
            }
        }
    }
}
