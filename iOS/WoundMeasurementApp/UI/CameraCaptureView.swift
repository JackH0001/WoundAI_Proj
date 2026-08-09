import SwiftUI
import AVFoundation
import UIKit

/**
 相機拍攝（對等 Android `CameraCaptureScreen`：CameraX 全解析擷取）。

 臨床現場以**拍照**為主——事後從相簿補件的照片可能經過壓縮或裁切，會破壞 ArUco 尺度鏈。
 這裡用 AVFoundation `.photo` preset 全解析擷取；方向由 `UIImage(data:)` 讀 EXIF 保留，
 再由 `MeasureViewModel.normalizeForBackend` 烘進像素（輸出一律 `.up`）。

 模擬器沒有相機：`AVCaptureDevice.default` 回 nil → 顯示明確訊息而不是黑畫面。
 */
struct CameraCaptureView: View {
    let onCapture: (UIImage, DepthCapture?) -> Void
    let onCancel: () -> Void

    @StateObject private var cam = CameraModel()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if cam.ready {
                CameraPreview(session: cam.session)
                    .ignoresSafeArea()
            }
            VStack {
                if let e = cam.error {
                    Text(e)
                        .font(.footnote).foregroundStyle(.white)
                        .padding(10)
                        .background(Color.red.opacity(0.7))
                        .cornerRadius(8)
                        .padding(.top, 8)
                }
                Text("請讓校正貼紙**完整入鏡且清晰**（約佔畫面 1/6）——沒有貼紙就沒有尺度，面積無法回推。")
                    .font(.caption).foregroundStyle(.white.opacity(0.9))
                    .padding(8)
                    .background(Color.black.opacity(0.45))
                    .cornerRadius(8)
                    .padding(.horizontal)
                // WoundAI3D：深度擷取狀態常駐顯示——「有沒有收到深度」不該事後才發現。
                Text(cam.depthSupported
                     ? "📐 LiDAR 深度：擷取中（隨照片加密保存，供 3D 重建研究）"
                     : "📐 此裝置無 LiDAR 深度（照片照常可量測）")
                    .font(.caption2)
                    .foregroundStyle(cam.depthSupported ? .green : .white.opacity(0.7))
                    .padding(6)
                    .background(Color.black.opacity(0.45))
                    .cornerRadius(6)
                Spacer()
                HStack {
                    Button("取消") { cam.stop(); onCancel() }
                        .buttonStyle(.bordered).tint(.white)
                    Spacer()
                    // 快門：拍到後先 stop 再回呼，避免關閉動畫期間 session 還在跑。
                    Button {
                        cam.capture { img, depth in
                            cam.stop()
                            onCapture(img, depth)
                        }
                    } label: {
                        Circle()
                            .strokeBorder(Color.white, lineWidth: 4)
                            .frame(width: 72, height: 72)
                            .background(Circle().fill(Color.white.opacity(cam.ready ? 0.85 : 0.2)))
                    }
                    .disabled(!cam.ready)
                    Spacer()
                    // 佔位讓快門置中
                    Button("取消") {}.buttonStyle(.bordered).hidden()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 30)
            }
        }
        .task { await cam.start() }
        .onDisappear { cam.stop() }
    }
}

@MainActor
final class CameraModel: NSObject, ObservableObject {
    let session = AVCaptureSession()
    @Published var ready = false
    @Published var error: String?
    @Published var depthSupported = false

    private let output = AVCapturePhotoOutput()
    private var completion: ((UIImage, DepthCapture?) -> Void)?
    private var configured = false

    func start() async {
        let granted = await AVCaptureDevice.requestAccess(for: .video)
        guard granted else {
            error = "相機權限未授權。請到 iOS 設定 → WoundAI 開啟相機權限。"
            return
        }
        if !configured {
            // WoundAI3D：優先用 LiDAR 深度相機（RGB＋絕對深度同步擷取）；
            // 無 LiDAR 機型退回廣角——量測功能不受影響，只是收不到深度。
            let dev = AVCaptureDevice.default(.builtInLiDARDepthCamera, for: .video, position: .back)
                ?? AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
            guard let dev,
                  let input = try? AVCaptureDeviceInput(device: dev),
                  session.canAddInput(input) else {
                error = "找不到可用相機（模擬器沒有相機，請改用「相簿」或「檔案」）。"
                return
            }
            session.beginConfiguration()
            session.sessionPreset = .photo     // 全解析：ArUco 貼紙要清晰可辨
            session.addInput(input)
            if session.canAddOutput(output) { session.addOutput(output) }
            // ⚠ 順序固定：depth delivery 要在 output **加進 session 之後**才查得到支援與否。
            if output.isDepthDataDeliverySupported {
                output.isDepthDataDeliveryEnabled = true
                depthSupported = true
            }
            session.commitConfiguration()
            configured = true
        }
        // startRunning 會阻塞呼叫緒——不可在主執行緒上跑。
        let s = session
        await Task.detached { s.startRunning() }.value
        ready = true
    }

    func stop() {
        let s = session
        Task.detached { if s.isRunning { s.stopRunning() } }
    }

    func capture(_ done: @escaping (UIImage, DepthCapture?) -> Void) {
        completion = done
        let settings = AVCapturePhotoSettings()
        // 只有 output 已啟用時才能開，否則直接 crash（Apple 的執行期斷言）。
        if depthSupported { settings.isDepthDataDeliveryEnabled = true }
        output.capturePhoto(with: settings, delegate: self)
    }
}

extension CameraModel: AVCapturePhotoCaptureDelegate {
    nonisolated func photoOutput(_ output: AVCapturePhotoOutput,
                                 didFinishProcessingPhoto photo: AVCapturePhoto,
                                 error: Error?) {
        guard error == nil, let d = photo.fileDataRepresentation(),
              let img = UIImage(data: d) else {
            Task { @MainActor in self.error = "拍攝失敗：\(error?.localizedDescription ?? "未知錯誤")" }
            return
        }
        // 深度在背景緒轉出（320×240 f32 拷貝，毫秒級），失敗不影響照片本身。
        let depth = photo.depthData.flatMap { DepthCapture.from($0) }
        Task { @MainActor in
            self.completion?(img, depth)
            self.completion = nil
        }
    }
}

/// AVCaptureVideoPreviewLayer 的 SwiftUI 包裝。layer 直接掛 session，不經 SwiftUI 繪圖。
private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class PreviewUIView: UIView {
        override class var layerClass: AnyClass { return AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { return layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewUIView {
        let v = PreviewUIView()
        v.previewLayer.session = session
        v.previewLayer.videoGravity = .resizeAspectFill
        return v
    }
    func updateUIView(_ uiView: PreviewUIView, context: Context) {}
}
