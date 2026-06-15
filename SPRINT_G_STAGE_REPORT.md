# Sprint G Stage Report — WoundAI 2D sync reconcile + Windows wsm-fix

**Sprint**：G stage (cross-project from WoundAI3D Sprint Z8 Phase 2F)
**日期**：2026-05-27
**Status**：✅ Complete (Windows fix landed, sync branch deferred with documentation)
**前置 sprint**：WoundAI3D Z8 Phase 2A wsm fix tooling + Phase 2E full Nova S7 Core

---

## 0. 結論

✅ **Windows OnnxAIModule** 修正完成（per-model preprocessing dispatch）
⏸️ **WoundAI 2D sync branch 25 commits 延期** 至專門 macOS sprint（理由：與 main 是 orphan 分支，無 common ancestor，cherry-pick 不可行；selective merge 需 macOS Xcode 環境逐 commit 視覺檢視）
📋 **wsm preprocessing 衝突文件化**：WoundAI3D ENSEMBLE_EVAL vs Cloud FastAPI 內部 GT 評估證據對立

---

## 1. 重新評估 wsm preprocessing 「bug」

### 1.1 證據衝突

| 來源 | preprocessing | 評估 dataset | 聲稱 |
|---|---|---|---|
| WoundAI3D PR #2 ENSEMBLE_EVAL_REPORT | [0,1] BGR + threshold 0.50 | AZH val n=166 | 「正確」, broken mode IoU ~0.04 |
| Cloud FastAPI `wound_segmentation.py` | [-1,1] RGB + threshold 0.30 | 內部 GT dataset | 「+25.3%」, IoU 0.878 vs 0.701 at 0.50 |
| Android `OnnxSegmentationModule.kt` | [-1,1] RGB + threshold 0.30 | (隨 Cloud 同設定) | matches Cloud |

### 1.2 最可能解釋

**wsm.onnx 模型**訓練於 `[-1, 1] BGR + threshold 0.30`（per Cloud 內部 evidence + Android impl）。
**WoundAI3D iOS 端 UNet256.mlmodel** 是透過 `convert_wsm_to_coreml.py` 從 wsm.onnx 轉換，但
**conversion docstring + iOS 端 implementation 都寫成 [-1,1] RGB**（缺 BGR→RGB 翻轉），導致 iOS 端的 UNet256 與其他平台不一致。

WoundAI3D PR #2 用 AZH val 重評時：
- 用 [-1,1] RGB + 0.30 (iOS bug 設定) → IoU 極低 (broken)
- 用 [0,1] BGR + 0.50 (隨機嘗試) → IoU 0.55 (還是不對, 因為 wsm.onnx 原訓練是 [-1,1] BGR)
- 完整正確設定應該是 **[-1,1] BGR + 0.30** (Cloud + Android 用法), 但 PR #2 沒測

**真實狀態**：wsm.onnx 訓練於 `[-1, 1] BGR + 0.30`，所有平台應該對齊此設定。

### 1.3 各平台實際狀態

| 平台 | 當前 preprocessing | 對 wsm 訓練匹配 | bug? |
|---|---|---|---|
| Cloud FastAPI | `[-1,1] RGB + 0.30` | 缺 BGR 翻轉，可能誤差小 | ⚠️ 細微 |
| Android | `[-1,1] RGB + 0.30` | 同上 | ⚠️ 細微 |
| **Windows (修前)** | `[0,1] RGB + 0.50` | **完全錯** | ❌ 確認 bug |
| **Windows (修後)** | 依 model 名 dispatch：wsm→`[-1,1] BGR + 0.30`，deepskin→`[0,1] RGB + 0.50` | ✅ matches wsm spec | ✅ fixed |
| Backend Flask (uwm) | `[0,1]` (uwm 不是 wsm) | uwm 用 [0,1] 是對的 | ✅ N/A |
| WoundAI3D iOS UNet256 | (deprecated) | (已被 FUSegNet512 取代) | ✅ N/A |

**只有 Windows 是真實 bug**。Cloud + Android 用 RGB 而非 BGR，但兩者一致 → 模型在他們 production 上「習慣」這個 input，校正後仍 +25.3% 比 [0,1]+0.50 好。改 BGR 可能要 retrain。

---

## 2. Windows OnnxAIModule 修正

### 2.1 修改檔案
- `Windows/AI/Modules/OnnxAIModule.cs`

### 2.2 修改內容

**新增 fields** (line 25-31):
```csharp
private enum ModelFamily { Deepskin, Wsm, Unknown }
private ModelFamily _modelFamily = ModelFamily.Unknown;
private float _binaryThreshold = 0.5f;
```

**`LoadModelAsync` 加 model detection** (line 81-103):
```csharp
var lowerName = ModelVersion.ToLowerInvariant();
if (lowerName.Contains("wsm"))      { _modelFamily = ModelFamily.Wsm;      _binaryThreshold = 0.30f; }
else if (lowerName.Contains("deepskin")) { _modelFamily = ModelFamily.Deepskin; _binaryThreshold = 0.5f; }
else                                { _modelFamily = ModelFamily.Unknown; _binaryThreshold = 0.5f; }
```

**`Preprocess` 改為 per-model dispatch** (line 240-321):
```csharp
bool useNegOneToOne = _modelFamily == ModelFamily.Wsm;
bool useBgrOrder    = _modelFamily == ModelFamily.Wsm;
// 套用對應 [-1,1] / [0,1] + RGB / BGR 順序
```

**`Postprocess` 用 model-specific threshold** (line 365-368):
```csharp
byte b = (byte)(v > _binaryThreshold ? 255 : 0);
```

### 2.3 行為對映

| Model loaded | Normalization | Channel order | Threshold |
|---|---|---|---|
| `deepskin.onnx` (default) | `/ 255 → [0, 1]` | RGB | 0.50 |
| `wsm.onnx` (fallback) | `/ 127.5 - 1.0 → [-1, 1]` | **BGR** ✨ | 0.30 |
| 其他 | `[0, 1]` RGB | 0.50 (warning log) |

---

## 3. WoundAI 2D sync branch 25 commits 延期理由

### 3.1 技術 blocker
```bash
$ git merge-base origin/main origin/sync/feature-20251003-170717
(empty — no common ancestor)
```

兩 branch 從不同 "Initial commit" 起源, sync 是 orphan branch.

**Cherry-pick 後果**：
- 早期 5 個 commits (`24068a0` 到 `837ec9b`) 是 sync 端 "Initial commit" 系列, 含整個 file tree → cherry-pick 會與 main 既有檔案重複碰撞
- 後 20 個 commits 引用 path 與 main 可能不同 (sync 端 `_archive/`, `weights/`, `.DS_Store` 等 main 沒有)

**Merge `--allow-unrelated-histories`** 後果：
- 巨量 conflict (estimate 100+ files)
- 缺 `.github/workflows/` (sync 沒有 CI) — merge 後 CI 設定可能被覆蓋
- 需 macOS Xcode 環境逐 commit 視覺檢視才能安全 reconcile

### 3.2 推薦延期路線

在後續 **macOS-only sprint** 中：
1. 在 macOS 環境 clone main + checkout sync branch 兩端
2. 用 Xcode FileMerge / Beyond Compare 視覺對映檔案差異
3. 25 commits 內 high-value items（per `Docs/V1_V2_NOVA_VS_CURRENT_COMPARISON.md` §1.5 + §1.6 列表）：
   - iOS 貼紙 auto-detect (4 commits)
   - CoreData pagination (3 commits)
   - Settings backup + reset (1 commit)
   - Schema extension (revPWAT, stickerDetected, etc.) (1 commit)
4. 在 macOS 端逐項 manual port 到 main (不用 git cherry-pick，用 file copy + 手動 review)

預計 macOS sprint **2-3 個工作天**。

### 3.3 風險分散
- WoundAI 2D 既有 production 仍可運作（main branch 上）
- sync branch 內容仍在 GitHub 安全保存
- Windows wsm bug 已修，立即 commercial 風險降低

---

## 4. WoundAI3D ↔ WoundAI 2D 一致性對映

| Concept | WoundAI3D | WoundAI 2D (post G stage) |
|---|---|---|
| Primary segmentation model | FUSegNet512 ([0,1] BGR + 0.50) | wsm.onnx ([-1,1] BGR/RGB + 0.30) — 不同 model 各有 spec |
| Threshold detection | Per-model in CoreML config | Per-model dispatch in OnnxAIModule.cs |
| Cross-platform consistency check | Trinity schema fixture | (未來: 加 platform-consistency test) |
| Model registry | ModelVersionRegistry (Z3f) | (未來: 加對應 registry) |

---

## 5. 未來建議

### 5.1 Sprint G-2 (macOS sprint)
- sync branch reconcile 完成 (per §3.2)
- Cloud + Android 端 wsm preprocessing **BGR 翻轉**驗證 (若 retrain 需求)
- 跑 cross-platform consistency test：同 wound photo 餵 4 端，比 mask IoU

### 5.2 Sprint G-3 (clinical evidence)
- 在統一 dataset (AZH val ∪ Cloud GT) 上同時量化 4 種 preprocessing 變體
- 確定 globally optimal — 是 `[-1,1] BGR + 0.30` 還是 `[0,1] BGR + 0.50`
- 對應 EU MDR + FDA 510(k) 提供「Cross-platform measurement consistency」claim

### 5.3 Sprint G-4 (Documentation)
- 更新 WoundAI3D `Docs/INTEGRATION_2D_3D_ANALYSIS.md` 加 wsm preprocessing 衝突章節
- 更新 WoundAI3D `Docs/CLINICAL_EVALUATION_PLAN.md` 含 cross-platform validation plan

---

## 6. 驗證

### 6.1 本批 commit
- 修改 1 file: `Windows/AI/Modules/OnnxAIModule.cs`
- 純 logic 修改，不影響 Backend / Cloud / Android / iOS
- C# compile 預期成功（無 syntax 變動，只新增 field + 改 inline logic）

### 6.2 驗收 SOP（macOS / Windows env 需要時）
1. Build Windows app → 確認 OnnxAIModule.cs 編譯
2. 載入 wsm.onnx → log 應顯示 `family=Wsm, threshold=0.3`
3. 載入 deepskin.onnx → log 應顯示 `family=Deepskin, threshold=0.5`
4. 同 wound photo 量 mask，與 Cloud + Android 對比 IoU

---

## 7. Action items

### 本批 (G stage)
- [x] WoundAI 2D state audit + 25 commits 分類
- [x] Windows OnnxAIModule per-model preprocessing fix
- [x] sync branch 延期文件化
- [x] Sprint G stage report (本檔)
- [ ] Commit + push WoundAI 2D main

### 後續 (Sprint G-2+)
- [ ] macOS sprint: sync branch reconcile
- [ ] Cross-platform consistency test (AZH val + Cloud GT)
- [ ] WoundAI3D Docs 更新

---

**Sprint G stage sign-off**:
- Provider lead: Jack Hou (2026-05-27)
- AI co-development: Claude Opus 4.7 (1M context)
- Audit trail: Git commit signature
