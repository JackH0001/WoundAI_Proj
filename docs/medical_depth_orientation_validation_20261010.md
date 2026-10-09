# 醫療版方向沿革與 WoundAI3D 交接：2026-10-10

本次從公開協作庫 `f974e2a`／母庫 `1091eac` 開始核對，確認 WoundAI3D 回報的醫療版缺口：`fromPhoto` 已將 EXIF 轉換套用至深度與 Apple 校正資料，但 `sourceExifOrientation` 原先只留在記憶體，未進入 sidecar 或 `/api/v1/depth`。

## 修正契約

醫療版 `DepthCapture.metaJson` 新寫 sidecar **version 2**；既有檔案不改寫、不猜測、不重傳。

| 欄位 | 新拍且來源明確 | 舊資料／來源未知 |
|---|---|---|
| `source_exif_orientation` | 原始 EXIF 1–8 | 省略 |
| `normalized_exif_orientation` | 1（已套用轉換） | 省略 |
| `orientation_status` | `normalized` | `unknown` |
| `registration` | `not_verified` | `not_verified` |

`BackendClient.uploadDepth` 使用可獨立測試的 metadata mapper，把同一組沿革放進真實 multipart 請求。舊 sidecar 仍可讀取與依既有規則保存，但傳遞為 unknown，不假裝完成配準。來源值超界、normalized 方向不為 1 或狀態矛盾時，不輸出可誤認為正向的欄位。

後端現行深度端點會保存額外欄位，這次沒有修改後端 API 或部署映像。本機 HTTP 測試逐一讀回八種來源方向的 `.meta.json` 及原始 f32 bytes，確認未被濾除。**後端目前不是方向真偽的嚴格驗證器**；新增欄位是客戶端提供的沿革，不能當成雲端已驗證 registration 的證據。

內參仍依既有 schema：sidecar `intrinsics.ref_w/ref_h` → upload `camera_intrinsics.ref_width/ref_height`。已知非對稱內參 fixture 只驗證這些值經 JSON／HTTP／落盤不遺失，不宣稱驗證 Apple 內參旋轉。`captured_at` 既有欄位是在 meta 產生時填入，亦不能當成 RGB／depth 的硬體同步時間戳。

## 本輪實測

- 醫療版 XCTest：**9/9**。包含共用八方向 4 項、方向保存／上傳 5 項。RGB fixture 改為 3×2 六種不同色塊，遮罩為非對稱三格；對所有八種 EXIF 逐格比對 RGB、遮罩與不同深度值，不只比長寬。
- Lite XCTest：**14/14**。同一份方向測試實際呼叫 Lite 正規化路徑，另跑原始封包與保存收據。
- 加密 sidecar 真實寫入／讀回，以及 URLProtocol 攔截**實際 uploadDepth 請求**，確認 multipart 內有原始方向與未驗證配準狀態。
- 醫療後端既有深度檢查 **132/132**；新增 HTTP／保存測試 **2/2**（八方向＋legacy／unknown）。
- Lite 後端原始深度契約 **14/14**、HTTP **9/9**。
- 受控變異 **2/2 被抓到**：忽略 RGB 水平鏡像（尺寸仍相同）、把上傳來源方向寫死為 1，指定新增測試皆失敗；已還原後重跑。
- 新增 HTTP 保存測試已掛入 `p0-4-audit.yml` 的執行步驟及 push／PR paths。醫療與 Lite 兩個 XCTest target 均掛入共用方向測試。

模擬器採現有 CI 的 ad-hoc signing，讓 Keychain 有 App identity。初次未簽署測試的 Keychain 失敗、同步目錄的 Finder xattr 簽署失敗均保留日誌；改用 `/private/tmp` DerivedData 及 ad-hoc signing 後驗證通過。第一版 HTTP fixture 的 store singleton 未隔離造成第二例失敗，現已每例注入並還原獨立 LocalStore。

## 仍未完成的驗收

1. **帶真實 Apple cameraCalibrationData 的八方向 fixture**：目前合成 AVDepthData 不含校正資料，測試明確驗其缺失，不製造假的校正物件。需要不含個資的實體標靶 RGB-D、完整校正資料及來源紀錄，逐點驗內參／射線轉換。
2. **真機安裝版本**：本輪 devicectl 讀取 iPhone App 清單逾時，未確認目前安裝 build，也未安裝或上傳新版 App。
3. **端到端配準**：同一不對稱實體標靶、同 capture 的 RGB／depth／mask／內參；正向、鏡像與 90°／180° 的角點對照。拍螢幕只能作流程或螢幕平面測試，不能當人體傷口 3D 真值。
4. **WoundAI3D 入庫**：方向未知資料保持隔離；即使本次來源方向已知，registration 仍是 not_verified。不能在 adapter 改成 verified/aligned、補猜 pose 或補造同步時間戳。完整 RGB-D／組織分類／雜湊綁定／revision／同意與撤回仍需按原交接逐筆驗證。

本機證據：`woundai-institution-evidence-20261007/depth-provenance-20261010/` 的 XCTest `.xcresult`、醫療／Lite log、storage-tests.log 與三份後端契約 log。本次沒有操作雲端研究資料、部署或鎖桶。

Apple 官方：[applyingExifOrientation](https://developer.apple.com/documentation/avfoundation/avdepthdata/applyingexiforientation(_:)) 會轉換深度圖與校正資料；[cameraCalibrationData](https://developer.apple.com/documentation/avfoundation/avdepthdata/cameracalibrationdata) 說明由檔案或人工修改而來的深度可能缺校正資料。因此像素方向測試與校正 fixture 驗收必須分開。
