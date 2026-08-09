# WoundAI3D 深度資料擷取規劃 — iOS LiDAR 作為 3D 重建的資料地基

**日期**：2026-08-09　**狀態**：MVP 已實作（單張 RGB-D），待實機驗證
**定位**：iOS 相對 Android 的戰略優勢是 LiDAR／TrueDepth 硬體。本文件定義**現在就開始收什麼**、
**怎麼存**、**怎麼上傳**，讓 WoundAI3D（3D 建模・體積量測・曲率分析）開案時已有臨床資料集，
而不是從零開始收。

---

## 一、為什麼是「現在收、以後用」

深度圖與相機內參只存在於**拍攝當下**——事後補不回來。臨床收案每一筆順手收下（醫師工作流
零改變），n=20 收完時 WoundAI3D 就有 n=20 筆 RGB-D。反過來等專案開案再收，等於再走一輪 IRB
與收案週期。

## 二、資料清單（每筆量測要收什麼）

| 資料 | 用途 | MVP | 來源 |
|---|---|---|---|
| **深度圖** Float32（公尺）、LiDAR 絕對深度 | 表面重建、傷口體積、深度分佈 | ✅ 已收 | `AVCapturePhotoOutput` depth delivery |
| **相機內參** fx/fy/cx/cy＋參考解析度 | 反投影成點雲（X=(u−cx)·Z/fx），沒有它深度圖建不了模 | ✅ 已收 | `AVCameraCalibrationData.intrinsicMatrix` |
| 深度精度標記 absolute/relative＋filtered 旗標 | 資料集分層（LiDAR vs 視差；濾波會抹平小凹陷） | ✅ 已收 | `AVDepthData` |
| 覆蓋率／最小最大距離摘要 | 不解檔快篩（覆蓋率過低先排除） | ✅ 已收 | 擷取時計算 |
| RGB work 影像尺寸 | 深度↔輪廓座標對齊宣告 | ✅ 已收 | classify 回應 |
| 重力向量（裝置姿態） | 點雲擺正、跨次配準的初始化 | 🔜 P2 | CoreMotion 一次採樣 |
| 鏡頭畸變表 lensDistortionLookupTable | 高精度反投影（邊緣像素） | 🔜 P2 | `AVCameraCalibrationData` |
| 多視角序列（ARKit worldTracking 每幀 pose＋depth） | 完整 3D 網格（photogrammetry 級） | 🔜 P3，獨立拍攝模式 | ARKit `ARDepthData` |

MVP 刻意選**單張 RGB-D**：醫師工作流完全不變（按一次快門），資料已足以做體積估計與
曲率分析的先導研究；多視角掃描是另一個拍攝 UX，列 P3 獨立規劃。

## 三、儲存設計（已實作）

- **不動 `measurements` schema**：DB v6 與 Android Room v6 鏡像，深度是 iOS 獨有，加欄位會讓
  兩端 schema 分岔。改用 sidecar：`depth_index.json`（僅檔名對映，無 PHI）把
  `Measurement.imagePath` 映射到兩個 **AES-GCM 加密檔**（深度圖 f32 原樣＋meta JSON）。
- **與影像同保存政策**：90 天清除與同圖去重更新時，sidecar 一併清（`purgeExpiredImages`
  與 `saveToTimeline` 的 UPDATE 分支都已接）。
- 格式：`f32_le_meters` 原樣不壓縮不量化——量化損失對曲率是不可逆的。LiDAR 深度圖
  ≈320×240×4B≈300KB/筆，n=20 全收 <10MB，儲存壓力可忽略。
- meta JSON 內容：version、尺寸、intrinsics、accuracy、filtered、rgb_w/h、coverage、
  min/max 距離、時間戳、機型。

## 四、上傳協定（`depth_source` 詞彙，已實作真值）

後端 annotation 欄位 `depth_source` 原本硬編碼 `"none"`，現在是三值真值：

| 值 | 意義 |
|---|---|
| `none` | 沒拍深度（相簿／檔案來源、無 LiDAR 機型） |
| `lidar_local` | **拍了、加密存本機、尚未上傳**（現況） |
| `lidar` | 已隨標註上傳（後端深度端點上線後） |

「沒拍」與「拍了沒傳」分得出來，日後盤點資料集才知道哪些樣本可以回收深度。

**後端端點提案（P2，後端工作）**：`POST /api/v1/depth`
`multipart: {image_id, depth_f32(raw bytes), meta(json)}`；以 `image_id` 綁定既有影像；
守門同 annotation（JWT＋consent_train）；存 GCS `depth/` 前綴；回 `depth_id`。
App 端屆時把 sidecar 補傳並將 `depth_source` 升級為 `lidar`（本機 sidecar 保留為原始檔）。

## 五、驗證計劃

**實機（J-iP16PM 有 LiDAR）**：拍照畫面應顯示「📐 LiDAR 深度：擷取中」→ 拍攝→分析→存入
時間軸 → saveNote 顯示「（含 LiDAR 深度）」→ 送標註後可在後端佇列 jsonl 看到
`depth_source: "lidar_local"`。距離合理性抽查：meta 的 min/max 應落在拍攝距離 ±20%
（傷口近拍通常 0.2–0.5m）。
**模擬器**：無相機——只能驗「無 LiDAR」退化路徑與 `none` 標記。
**資料品質基準（P2）**：對已知尺寸的 phantom 拍 RGB-D，反投影點雲量測平面距離誤差，
建立「LiDAR 在近拍距離的精度基線」（Apple 規格外的實測值，論文要用）。

## 六、風險與邊界

- LiDAR 深度解析度低（≈256×192）：足夠體積／曲率，不足以取代 RGB 輪廓——分割仍以 RGB 為準。
- `builtInLiDARDepthCamera` 的 RGB 視角與純廣角略有差異；depth 與 photo 由同一 device 同步
  輸出，Apple 保證對齊到 photo 座標（`AVDepthData` 隨 photo 遞交），不需自行配準。
- 深度屬去識別幾何資料，但仍與影像同等加密、同保存政策——法遵姿態從嚴。
- 濾波（filtered）預設關閉時有洞（反光／吸光材質），照實記錄 coverage 讓研究端取捨。
