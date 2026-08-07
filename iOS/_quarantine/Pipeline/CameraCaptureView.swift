import SwiftUI
import AVFoundation
import UIKit

/**
 iOS 高解析拍攝(AVFoundation,對等 Android CameraCaptureScreen)：預覽 + 拍攝 → CGImage → [MeasureViewModel].analyze。
 需求：Info.plist 加 NSCameraUsageDescription；執行期請求相機權限。校正貼紙需清晰可見。
 輔助、非診斷、需醫師確認。
 */
final class CameraController: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
    let session = AVCaptureSession()
    private let output = AVCapturePhotoOutput()
    private var onImage: ((CGImage) -> Void)?

    func configure() {
        session.beginConfiguration()
        session.sessionPreset = .photo
        if let dev = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
           let input = try? AVCaptureDeviceInput(device: dev), session.canAddInput(input) {
            session.addInput(input)
        }
        if session.canAddOutput(output) { session.addOutput(output) }
        session.commitConfiguration()
    }
    func start() { if !session.isRunning { DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() } } }
    func stop() { if session.isRunning { session.stopRunning() } }

    func capture(_ completion: @escaping (CGImage) -> Void) {
        onImage = completion
        output.capturePhoto(with: AVCapturePhotoSettings(), delegate: self)
    }
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        guard error == nil, let data = photo.fileDataRepresentation(),
              let ui = UIImage(data: data), let cg = ui.cgImage else { return }
        onImage?(cg)   // UIImage 已處理 EXIF 方向;CGImage 供管線
    }
}

/// AVCaptureVideoPreviewLayer 預覽。
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewUIView {
        let v = PreviewUIView()
        v.videoPreviewLayer.session = session
        v.videoPreviewLayer.videoGravity = .resizeAspectFill
        return v
    }
    func updateUIView(_ uiView: PreviewUIView, context: Context) {}
    final class PreviewUIView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}

struct CameraCaptureView: View {
    @ObservedObject var vm: MeasureViewModel
    @StateObject private var cam = CameraController()

    var body: some View {
        ZStack(alignment: .bottom) {
            CameraPreview(session: cam.session).ignoresSafeArea()
            Button(action: {
                cam.capture { cg in Task { await vm.analyze(image: cg, exudate: nil) } }
            }) {
                Text("拍攝").bold().padding(.horizontal, 28).padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent).padding(.bottom, 30)
        }
        .onAppear { cam.configure(); cam.start() }
        .onDisappear { cam.stop() }
    }
}
