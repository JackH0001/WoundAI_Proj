# P0-4 Phase A：本地候選交接（2026-08-31）

狀態：第一批實作與本地回歸，**不是 Phase A 完整交付／部署核准**。
分支 `codex/p0-4-staging-promotion`，基底 `505ff2e69f68a3b263f16adca8608a4036d5953f`。
設計來源 `P0-4_design_v2.3.5.md` SHA-256
`d71a0ad154aca5331ab209509f791d6a92a445c21f00454c376f5350aca7f9cd`，前版差分依取代關係適用。

## 本輪範圍

- `image_canonical.py`：JPEG allowlist、metadata/方向處理、透明 PNG 拒收、分析 decode(canonical)。
- `consent_staging.py`：care HMAC envelope、org/角色檢查、短期 bind／長期 promotion receipt、
  Layer A/B 分派、receipt-first/queue-last、withdraw 重驗、repair 與 sweep 分離。
- `store.py`：Local/GCS create-only 收據、JSON mime/readback、受保護鍵分流与刪移守衛、
  一筆 annotation receipt 只入列一次；GCS 稽核讀取失敗不再略過。
- classify 與 annotation 已接入；`classify_queue` 同點控制兩個匯出器與 manifest 的 legacy 排除。
- Android/iOS 最小 attest+classify 流程，實際上傳前重讀照護同意；demo code 於量測時建立、
  送標註／時間軸沿用；量測後切換個案不得把已綁定影像存到另一個案。
- list-only inventory、分開 sweep／repair CLI；archive 遇 staging 非空中止（含 dry-run）。
- 測試資料需新收據者僅使用暫存目錄內、明標 SYNTHETIC 的 fixtures；**不是實際 legacy 核准**。

## 證據與重跑

Python 全套（每個測試檔隔離進程；pytest-only 檔案真的以 pytest 執行）：

```powershell
. tools/windows/Enter-WoundAITestEnv.ps1 -RepoRoot C:\dev\WoundAI_Proj
python tools/windows/run_python_tests.py --repo C:\dev\WoundAI_Proj --out artifacts/windows-test/p0-4/final --timeout 120
python -m pytest engineering/phase2/test_p0_4_staging.py engineering/phase2/test_endpoint_guards.py -q -p no:cacheprovider
Android/gradlew.bat -p Android --offline --no-daemon :app:compileDebugKotlin :app:testDebugUnitTest
```

執行時需設定隔離的 `WOUNDAI_FLYWHEEL_DIR`，且功能測試用
`WOUNDAI_REQUIRE_FUNCTIONAL_TESTS=1`；不要對正式服務跑測試。
本轮結果、逐檔 log 與實算的 source SHA-256 見
`artifacts/windows-test/p0-4/final/python-summary.json`；摘要的
`source_snapshot_same_after` 必須 true。HEAD 相同不表示未提交內容相同。
Android XML 在 `Android/app/build/test-results/testDebugUnitTest/`；是既有 5 個演算法單元測試，
**不是新收據流程的裝置端 E2E 證明**。新端點 runtime 測試在 Python 合成測試內。

## 必須留給下一批，尚不可宣稱完成

1. **部署重現性**：本機 CPython3.13.5、opencv-contrib-python-headless5.0.0.93／cv2 5.0.0；
   設計實驗用4.13.0。現有 Dockerfile 仍 python:3.11-slim，Backend requirements 仍浮動範圍。
   要選定部署 runtime、完整 lock＋base digest，再在實際映像跑 golden-byte 與全套測試。
   現在的 `canonicalization_version` 誠實記實際 cv2，但尚非受批准的跨平台位元組基準。
   官方套件也明示 cv2 套件不可混裝，見 [OpenCV wheel 套件說明](https://pypi.org/project/opencv-python-headless/)。
2. **雲端 lifecycle 與硬化腳本**：`harden_bucket.ps1` 本輪未改、未執行；仍須加入來源一致的
   實體 prefix、staging30/meta37/noncurrent/soft-delete 設定與嚴格 describe readback。
   runtime retention 查詢已有，但 mock 通過不證明正式桶的 policy／lock。Bucket Lock 不可逆，未授權。
3. **Mac iOS build/test＋App E2E**：Swift 修改只有來源檢查，Windows 不能替代 Xcode。
   核對 nil receipt、失效 token、同 org 護理師→醫師交接、切換病患、demo code 重送與長期重標。
   重開全新量測會產生新的 demo code；若同 bytes 已綁另一 code，後端會拒絕重綁（fail-closed），
   demo 重用 UX 仍要驗收，不能為便利移除病患綁定。
4. **完整驗收**：多實例 GCS 失敗注入／併發 withdrawal、獨立覆核、部署 golden、App E2E 尚未完成。
   現有其他 sidecar 路徑／以 image_id 命名的組織遮罩仍沿用原契約，平行標註的遮罩保存需獨立檢視。
5. **Phase B**：exact-byte restage、restageable、本機存檔失敗提示及 TTL 收斂7天都未實作。
   P0-2 JWT 撤銷、P0-5 離線事件排序仍在後續；不得解除範例／模擬資料限制。

## 12 個既有 phase2 SHA-1 影響面

盤點不含本輪新測試：`archive_flywheel_queue.py`（說明）；`distill_pseudo_gen.py`（非 queue
匯出入口）；`test_depth_chain.py`、`test_depth_endpoint.py`、`test_multi_wound.py`、
`test_record_preview.py`、`test_records_scope.py`、`test_resubmit_from_timeline.py`、
`test_rbac.py`、`test_retract_and_records.py`、`test_tissue_dataset.py`、`test_tissue_export.py`
（合成 fixtures）。新影像 ID 為 canonical bytes 的 opaque ID，不能從 App 再編碼位元組推算。

## 覆核與授權邊界

Codex 本輪是實作者，**沒有自行提供獨立覆核簽名**。請 Claude 審本輪 diff 與上述缺口，
不能沿用前一輪的 Codex review trailer。未 commit、push、開 PR、改 CI workflow、merge、
執行雲端設定、部署或解除限制；母庫與 WoundAI3D 未改。這是供下一輪覆核的工作樹，不能直接上線。
