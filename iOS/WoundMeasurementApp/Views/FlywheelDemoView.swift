import SwiftUI
import UIKit

/// 薄切片 Demo 流程（資料飛輪）：選圖 → 取得 AI 初稿(/segment) → 醫師修邊 → 上傳標註(/annotations)。
/// 校正貼紙量測與修邊請接既有模組（StandardStickerCalibrationView / EnhancedAnnotationView）；
/// 本檔聚焦把流程串到新的標註飛輪 API，作為現場 demo 的最小可動路徑。
struct FlywheelDemoView: View {
    enum Step: String { case pick = "選擇/拍攝影像", segment = "AI 初稿(輔助)", edit = "醫師修邊", upload = "上傳標註", done = "完成" }
    @State private var step: Step = .pick
    @State private var image: UIImage?
    @State private var draft: SegmentationResult?
    @State private var record: AnnotationRecord?
    @State private var status: String = ""
    @State private var busy = false
    private let service = AnnotationFlywheelService()
    private let imageId = UUID().uuidString

    var body: some View {
        NavigationView {
            VStack(spacing: 16) {
                banner
                if let image { Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 260).cornerRadius(8) }
                Group {
                    switch step {
                    case .pick:    Button("使用示意影像開始") { image = UIImage(); step = .segment }
                    case .segment: actionButton("取得 AI 初稿（/segment）") { await runSegment() }
                    case .edit:    actionButton("送出修邊結果為標註（/annotations）") { await runUpload() }
                    case .upload:  ProgressView()
                    case .done:    doneView
                    }
                }
                if !status.isEmpty { Text(status).font(.footnote).foregroundColor(.secondary) }
                Spacer()
                Text("AI 為輔助、需醫師確認；缺模型時自動 manual_fallback，不偽造。")
                    .font(.caption2).foregroundColor(.secondary).multilineTextAlignment(.center)
            }
            .padding()
            .navigationTitle("傷口標註飛輪 Demo")
        }
    }
    private var banner: some View {
        HStack { Image(systemName: "info.circle"); Text("步驟：\(step.rawValue)") }
            .font(.subheadline).padding(8).background(Color.teal.opacity(0.12)).cornerRadius(8)
    }
    private var doneView: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("已建立標註紀錄").bold()
            if let r = record {
                Text("image_id: \(r.imageId)"); Text("area_px: \(r.areaPx)")
                if let iou = r.correctionIou { Text(String(format: "correction_iou: %.3f", iou)) }
                Text("status: \(r.status)")
            }
            Button("再來一次") { reset() }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func actionButton(_ title: String, _ work: @escaping () async -> Void) -> some View {
        Button { Task { busy = true; await work(); busy = false } } label: {
            HStack { if busy { ProgressView() }; Text(title) }
        }.disabled(busy).buttonStyle(.borderedProminent)
    }
    private func runSegment() async {
        guard let image else { return }
        do { let r = try await service.segment(image: image, modelId: "segmentation.wsm", imageId: imageId)
             draft = r; status = "初稿狀態：\(r.status)" + (r.confidence.map { String(format: "，信心 %.2f", $0) } ?? "")
             step = .edit
        } catch { status = "／segment 失敗：\(error)（可改 manual 手動標註）"; step = .edit }
    }
    private func runUpload() async {
        // demo：以 1x1 透明 PNG 之 base64 佔位；實機改為修邊後遮罩之 PNG base64
        let editedB64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        let submit = AnnotationSubmit(imageId: imageId, editedMaskPngB64: editedB64,
                                      editorId: "dr_demo", modelId: "segmentation.wsm", pxPerMm: 3.0)
        step = .upload
        do { record = try await service.submitAnnotation(submit); status = "已上傳"; step = .done }
        catch { status = "／annotations 失敗：\(error)"; step = .edit }
    }
    private func reset() { step = .pick; image = nil; draft = nil; record = nil; status = "" }
}
