# engineering/generated — 由 SSOT 自動產生的各端前處理常數（M1 接線）

**請勿手改本資料夾任何檔。** 全部由 `phase0/gen_preprocessing_constants.py` 讀 `phase0/preprocessing.json`(SSOT) 產生。

## 為何存在
稽核發現 iOS/Android/Windows **各自硬編碼前處理**(~84 處)、彼此不一致,且 SSOT 修正(如 wsm→[0,1]BGR)進不了產品。本機制讓**四端編譯期吃同一份 SSOT**:改 SSOT → 重跑產生器 → 各端同步。

## 產出
- `ios/Preprocessing.generated.swift`（enum Preproc）
- `android/Preprocessing.generated.kt`（object Preproc）
- `windows/Preprocessing.generated.cs`（static Preproc）
皆含每模型 input_size/layout/channel_order/normalize/threshold 與建議貼紙。

## 各端接線(一次性)
1. 把對應產生檔加入該端專案編譯(iOS target / Android sourceSet / Windows 專案)。
2. **移除各端原本硬編碼的前處理常數**,改引用 `Preproc.<model>`。
3. **載入模型後斷言** `model.input shape == Preproc.<model>.(w,h,layout)`;不符即拒用(graceful degrade),避免錯前處理產生錯遮罩。
4. CI 加一步:跑 `gen_preprocessing_constants.py` 後 `git diff --exit-code engineering/generated`(若有人改 SSOT 沒重生 → CI 紅)。

## 執行期斷言樣板
- iOS: `precondition(model.inputShape == [Preproc.smp.h, Preproc.smp.w])`
- Android: `require(interp.getInputTensor(0).shape().contentEquals(intArrayOf(1,Preproc.smp.h,Preproc.smp.w,3)))`
- Backend(Py): `assert sess.get_inputs()[0].shape[1:3]==[cfg['input_size']]`(已於 segment.py/seg_infer.py 行為)

> 規則:**任何端不得再出現前處理魔術數字**;一律來自本產生檔(來源 SSOT)。
