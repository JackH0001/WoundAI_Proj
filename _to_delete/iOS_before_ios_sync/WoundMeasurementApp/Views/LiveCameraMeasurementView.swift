import SwiftUI
import AVFoundation

struct LiveCameraMeasurementView: View {
    @Environment(\.presentationMode) var presentation
    @StateObject private var camera = LiveCameraSession()
    @State private var pixelAreaText: String = "—"
    @State private var cm2AreaText: String = "—"
    
    var body: some View {
        ZStack {
            LiveCameraPreviewLayer(session: camera.session)
                .ignoresSafeArea()
            
            VStack {
                HStack {
                    Button("關閉") { presentation.wrappedValue.dismiss() }
                        .padding().background(.black.opacity(0.4)).foregroundColor(.white).cornerRadius(8)
                    Spacer()
                }.padding()
                Spacer()
                
                HStack(spacing: 12) {
                    Text("像素面積: \(pixelAreaText)")
                    Text("cm²: \(cm2AreaText)")
                }
                .padding(10)
                .background(.black.opacity(0.5))
                .foregroundColor(.white)
                .cornerRadius(10)
                .padding(.bottom, 24)
            }
        }
        .onAppear { camera.start() }
        .onDisappear { camera.stop() }
        .onReceive(camera.$latestResult.compactMap { $0 }) { res in
            pixelAreaText = String(res.pixelArea)
            if let cm2 = res.cm2Area { cm2AreaText = String(format: "%.3f", cm2) } else { cm2AreaText = "—" }
        }
    }
}

final class LiveCameraSession: NSObject, ObservableObject {
    let session = AVCaptureSession()
    private let analyzer = LiveCameraAnalyzer()
    private let stickerDetector = StickerDetector()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let queue = DispatchQueue(label: "live.camera.queue")
    
    @Published var latestResult: LiveCameraAnalyzer.Result?
    
    func start() {
        configureIfNeeded()
        session.startRunning()
    }
    
    func stop() {
        session.stopRunning()
    }
    
    private func configureIfNeeded() {
        guard session.inputs.isEmpty else { return }
        session.beginConfiguration()
        session.sessionPreset = .high
        defer { session.commitConfiguration() }
        
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera),
              session.canAddInput(input) else { return }
        session.addInput(input)
        
        videoOutput.setSampleBufferDelegate(self, queue: queue)
        videoOutput.alwaysDiscardsLateVideoFrames = true
        if session.canAddOutput(videoOutput) {
            session.addOutput(videoOutput)
        }
    }
}

extension LiveCameraSession: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        // 每隔數幀跑一次貼紙偵測以更新比例（取原始緩衝轉為 UIImage）
        if Int(CACurrentMediaTime()) % 2 == 0 { // 約 1Hz 嘗試
            if let ui = LiveCameraAnalyzer.uiImage(from: pb) {
                stickerDetector.detectScaleCmPerPixel(in: ui) { [weak self] cmpp in
                    self?.analyzer.updateScale(cmPerPixel: cmpp)
                }
            }
        }
        analyzer.process(pixelBuffer: pb) { [weak self] res in
            self?.latestResult = res
        }
    }
}

struct LiveCameraPreviewLayer: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewView { PreviewView(session: session) }
    func updateUIView(_ uiView: PreviewView, context: Context) {}
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    init(session: AVCaptureSession) {
        super.init(frame: .zero)
        previewLayer.session = session
        previewLayer.videoGravity = .resizeAspectFill
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}
