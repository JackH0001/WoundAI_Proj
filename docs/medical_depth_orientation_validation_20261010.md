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

## 2026-10-10 後續：實機版本與 metadata 修訂追溯

以 `devicectl` 唯讀查得 J-iP16PM：WoundAI `com.woundai.app` **1.0 (27)**、WoundLite
`com.woundai.lite` **1.0 (33)**；未列出 MMH 專用 bundle。這些是裝置實際版本，
不是本次候選來源的安裝證據。初次沙箱內的裝置服務查詢逾時；取得裝置服務存取後成功，
因此本次沒有要求使用者重登 Apple 或改手機設定。

公開協作庫 `761701d` 的 CI **7/7 SUCCESS**；母庫 `b09acb0` **8 SUCCESS、1 SKIPPED**
（Android emulator），兩個 PR 仍為 Draft。這些結果綁定上述舊 head，不能沿用為以下新修正的 CI 結果。

### 重現與修正

對同一張合成 JPEG 的標註，兩次送入相同 Float32 bytes，僅把
`source_exif_orientation` 從 1 改為 8。未修正的 `/api/v1/depth` 會覆寫 `.meta.json`，
但 `depth_id`／索引 `sha256` 不變，並回 `replaced_previous=false`。同樣問題也適用於內參修改，
會把影響 3D 解讀的修訂誤稱為相同重送。

現在保留 `depth_id` 與索引 `sha256` 的既有「原始深度 bytes」意義，新增：

| 欄位 | 定義 |
|---|---|
| `meta_sha256` | 後端重算 coverage 後，實際寫入 `.meta.json` 的 UTF-8 bytes SHA-256；JSON 使用排序 key、無額外空白 |
| `payload_sha256` | `SHA256(UTF8("woundai-medical-depth-v1") + 0x00 + raw SHA256 的 32 bytes + meta SHA256 的 32 bytes)` |
| `payload_hash_version` | 1 |
| `depth_revision_id` | 回應中 `payload_sha256` 的前 16 個十六進位字元；驗證必須使用完整 hash |
| `comparison` | `first_upload`、`identical`、`changed`、`legacy_unverifiable` |

索引及寫入前稽核 intent 同時記錄深度／metadata／payload 雜湊；HTTP 回執提供 metadata／payload
雜湊。只有原始深度與 metadata 雜湊都相同，才說是 `identical`、`replaced_previous=false`。
舊索引沒有 metadata hash 時不補造歷史證據，記 `legacy_unverifiable` 並保守視為一次新覆寫
（`replaced_previous=true`），不是已證實 metadata 不同。

這是內容修訂辨識，**沒有改成跨物件交易或不可變版本儲存**。相同請求仍依原流程写入並追加稽核；
多個物件的競態與中途失敗不由這個 hash 修正解決。匯出端須把現存 f32／meta 重新計算後比對
同一筆索引，不一致就隔離。這組 hash 尚未綁定 RGB／遮罩／同意 revision，不能當成完整研究封包
的簽章，也不證明方向、內參或配準本身正確。線上後端仍是 `7ed425d`，本輪沒有部署。

### 本輪實測與證據

- HTTP／保存測試 **7/7**：八方向、legacy、JSON key 換序的相同重送、方向／內參修訂、
  實際落盤 hash 與 intent、舊索引不可證實、無效內參拒絕且不覆寫既有資產／索引。
- 同一份新測試在未修正 `761701d` 失敗；三個變異全部失敗：只比 raw hash、刪除 metadata 索引 hash、
  payload hash 忽略 metadata。固定版本控制組通過。變異均在暫存副本執行。
- 醫療深度既有檢查 **132/132**；Lite 原始深度契約 **14/14**、HTTP **9/9**；
  endpoint guards 以 **pytest 實際收集並執行 8/8**；audit chain 腳本 exit 0。
  最初直接執行 endpoint guards 檔案沒有收集 pytest 測試，已另用 pytest 重跑，不將前者計入通過數。
- 本輪沒有重跑前節 XCTest；前節醫療 9/9、Lite 14/14 是前輪證據。
- 新證據位於 `woundai-institution-evidence-20261007/depth-followup-20261010/`。
  裝置清單僅查本專案 App，沒有下載手機個案或研究影像。

本輪後端小範圍修正依 Jack 授權 Mac 繼續建置、驗證及必要對齊程序執行；
`owner_guard` 的 Backend→Windows 規則保留，不冒稱 Windows 實測或全面放寬所有權。

下一個實機關卡仍是：先發佈可辨識來源 SHA 的候選 App，再用不對稱實體標靶收集同 capture
的 RGB／遮罩／原生深度及真實 calibration。未完成前不將資料升為 WoundAI3D 可訓練真值。
