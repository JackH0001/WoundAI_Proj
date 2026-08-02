import SwiftUI
import UIKit

/// 相機／相簿選取器（SwiftUI 包裝 UIImagePickerController）。
///
/// 這是全專案唯一一份 ImagePicker。原本 CalibrationStickerView、
/// CalibrationSelectionView、StandardStickerCalibrationView、AnnotationView
/// 各自帶一份幾乎相同的實作，在單一 module 下必然衝突。
/// 合併後多了 `sourceType`，讓需要直接開相機的畫面不必再自己複製一份。
struct ImagePicker: UIViewControllerRepresentable {
    @Binding var selectedImage: UIImage?
    var sourceType: UIImagePickerController.SourceType = .photoLibrary

    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.delegate = context.coordinator
        // 模擬器沒有相機；來源不可用時安全退回相簿，避免直接崩潰。
        picker.sourceType = UIImagePickerController.isSourceTypeAvailable(sourceType)
            ? sourceType : .photoLibrary
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController,
                                context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate,
                             UINavigationControllerDelegate {
        let parent: ImagePicker
        init(_ parent: ImagePicker) { self.parent = parent }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let image = info[.originalImage] as? UIImage {
                parent.selectedImage = image
            }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}
