# Android MMH 私有入口與 iPlay 60 驗證（2026-10-11）

## 範圍與目前結果

獨立安裝「WoundAI MMH 驗證」1.0-mmh-validation (26)，套件 `com.woundai.app.mmhps20261007.validation`。原有 `com.woundai.app` 內測版及其資料保留。此 APK 使用 Debug 簽章，只供合成資料驗收，不能視為機構正式發行版。

使用者已在平板完成 Google 登入，畫面顯示「Google 與機構入口驗證通過」。雲端唯讀核對 `/api/health?android_entry_probe=1` 於 UTC 2026-10-11 02:42:29.724786（台北 10:42:29）回 HTTP 200，revision `woundai-backend-mmhps20261007-c9a4b9e49f414`。使用者隨後回報 WoundAI 帳號連線測試登入成功。後續合成實拍已入庫，影像／遮罩读回核對通過；App 保存確認因舊後端回覆缺修訂收據而拒絕，詳見下方。本輪不宣稱端到端驗收通過。

## 新增配置

使用者明確授權後，於 `woundai-jackh001` 建立 `MMHPS20261007 Android Validation` OAuth client：

- Client ID：`421209514056-s94f66cdrdk3nok9ms8p6r4o26p0lh46.apps.googleusercontent.com`。
- 套件：`com.woundai.app.mmhps20261007.validation`。
- APK 簽章 SHA-1：`A9:3D:72:C4:5D:97:E7:83:4B:CC:48:7C:59:37:12:D3:D6:1C:22:0B`，apksigner 讀回相符。
- Google token 使用既有 MMH Web audience；未新增 IAM、測試者、公開入口或資料權限。
- 固定候選位址：`https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`。release、internalTest 與一般 Debug 位址未改。

Google token 僅保存在程序記憶體；本機檢查 audience、issuer、nonce、效期，簽章與入口 IAM 由 Google/IAP 驗證。健康探測必須 HTTP 200、服務名稱相符及 revision 非空，才允許送 WoundAI 帳密。Google 憑證透過 Proxy-Authorization，WoundAI JWT 保留在 Authorization。僅允許固定 HTTPS origin 的 /api/ 路徑，不跟隨重新導向，不自動重試寫入。新登入或登出使舊 session 無效，並清除本機 WoundAI 登入資料。

限制：尚無 Google token 自動更新；App 程序重啟或到期需重新登入。Android 仍沒有 iOS 等同的持久研究 outbox／原始深度採集。平板送 `depth_source=none`，不能宣稱為完整 RGB-D。後端歷史修訂保存修正仍屬另一項尚未部署工作。

## 驗證

| 項目 | 本轮結果 |
|---|---|
| JVM | 51/51：入口 20、標註收據 23、送件 gate 3、計分／組織 5 |
| iPlay 60 一般 Debug | 16/16，0 skipped：合成幾何 3、設定頁 2、HTTP 保存回覆 6、等待按鈕 2、Room migration 3 |
| iPlay 60 MMH | 設定頁 2/2，0 skipped；Google 按鈕存在、門禁前帳密測試停用、返回／收鍵盤可用 |
| 建置與安裝 | MMH APK 與測試 APK 成功；獨立套件安裝並啟動 |
| verify_logic | 50/50 |
| 網址契約 | 8/8；新增 MMH profile，避免新網址被誤判為一般 Debug |
| parity | 未宣告差異 0；10 項已登記落差仍存在 |

合成幾何測試驗證 PNG 位元、非對稱位置、已知尺度 18 cm² 與 PUSH 13；不是相機實拍、AI 分割或臨床精度證據。初次測試把 18 cm² 的 PUSH 面積子分預期誤寫為 10，核對既有分級後修正測試為 9，未改計分邏輯。

首次 MMH 設定測試因新 build type 未帶 Compose test Activity，未進入頁面即失敗；補入 `mmhValidationImplementation(ui-test-manifest)` 後同兩項實機測試通過。使用者先前設定頁閃退尚未重現，未找到相符的 WoundAI crash 堆疊；不宣稱已確認其根因或修復。

XML 目錄另有過去同步留下的 `* 3.xml` 複本，排除於本轮計數並分開保全，51 項僅來自本輪五份標準報告。

`owner_guard --platform mac` 仍指出一份 Windows 所有權檔案 `engineering/phase2/test_backend_url_parity.py`。本次依使用者「授權必要對齊程序」及 Android MMH 對齊要求修改這份 Python 跨端網址檢查，記錄為單檔例外；不放寬 owner_guard。Windows 全套 PowerShell 驗證尚未執行。

## 操作

1. 開啟「WoundAI MMH 驗證」，勿與「WoundAI 驗證版」混淆。
2. 後端連線設定 → Sign in with Google，使用有 MMH IAP 權限的具名帳號。
3. 看到入口驗證通過後，輸入 MMH WoundAI 帳號與密碼，按「儲存」及「連線測試」。Google 管入口，WoundAI 管醫師／護理等角色。
4. 合成資料測試須另完成拍攝、醫師确认、時間軸保存及標註送出。等有效保存收據後才算成功；不以 HTTP 成功或登入成功代替。

證據位於 `../woundai-institution-evidence-20261007/android-mmh-entry-20261011/`：OAuth 畫面、建置／測試 logs、JVM XML、health-confirmed.json、summary.json 與 APK SHA-256。沒有保存 Google token、密碼或研究影像。

官方參照：[Android Credential Manager Google 登入](https://developer.android.com/identity/sign-in/credential-manager-siwg-implementation)、[IAP 程式化驗證與 Proxy-Authorization](https://docs.cloud.google.com/iap/docs/authentication-howto)。

## 實拍入庫與缺修訂收據診斷

使用者以「快速量測」並開啟模擬圖選項完成修邊送出。雲端紀錄：台北 10:45:51 care attest HTTP 200、10:46:03 classify HTTP 200、10:47:16 annotation HTTP 200（請求時間），佇列物件於 10:47:40 建立。

唯讀核對此新紀錄與其物件：

- source=phantom、role=physician、doctor_verified=true、consent_train=true。
- RGB 1536×2048、825,462 bytes；組織遮罩 PNG 3,064 bytes。
- 6/6 內容檢查通過：RGB 尺寸、PNG 格式、組織遮罩雜湊、輪廓雜湊、輪廓座標範圍、遮罩柵格尺寸。
- 32 點輪廓、面積約 16.20 cm²、滲液子分 0；未取得標靶真值，因此不是面積精度驗證。
- depth_source=none；沒有原始深度，不把此平板樣本當完整 RGB-D。
- 儲存紀錄本身有 annotation_receipt_id，但部署版本成功回覆只有 status/code/queue/image_id/note，未回傳 annotation_receipt_id。新版 Android 嚴格拒絕無收據的完成確認；不是使用者漏填深度或後端拒絕 phantom。

部署原始碼證據：`../woundai-institution-evidence-20261007/mmh-update-20261010/image-build/submission/context/api_flywheel.py` 的成功及重複回覆路徑。新來源已補上回執；本輪重新執行 `test_medical_revision_assets.py` **17/17 通過**，涵蓋修訂資產、相同重送、撤回、雜湊与部分寫入失敗。這仍是本機後端測試，不是新映像已部署。

目前候選仍為 c9a4b9e49f414；`tools/stage_mmh_revision.py` 的既有 extra_guards 拒絕 iapEnabled=true，因此不得直接拿舊部署器繞過門禁檢查。後續需完成保留既有 IAP／OAuth／IAM 的更新方案及測試，再重建候選、重送同一筆核對去重與保存收據。無須重新拍照或變更遮罩；不放寬 App 的收據驗證。

本輪沒有部署、修改 IAP/IAM 或改寫／刪除雲端資料。合成樣本不釋出為正式訓練資料。證據新增 `phantom-asset-readback.json`、`endpoint-status.json`、`android-mmh-backend-receipt-regression.log`。

## 2026-10-11 下午：候選收據部署與模擬圖歷史補送修正

後端 PR #23 已合併至 `b7f9dc2b28bc4a270c2df531838df18c69cfff07`；新候選 `cb7f9dc2b28bc` 已就緒，260/260 即時 IAM、隔離容器 42/42 HTTP 檢查通過。IAP/IAM 與預設 100% 流量未變。詳見本機 evidence 的 `mmh-iap-update-20261011/validation-report.md`。

使用者原紀錄在歷史頁被「未取得病患訓練同意」擋下：該頁只讀 caseId 的病患同意，沒有處理快速量測的未連結模擬圖；當 caseId=null 就一律拒絕，與首次量測路徑不一致。修正採用已保存的 source，不提供現場切換來源或補造同意：

- 僅 `source=phantom` 且 `caseId=null` 可依無病患模擬驗證流程補送；仍須醫師確認、影像綁定與嚴格保存收據。這是驗證資料，不作模型訓練或臨床收案。
- 有個案連結時，不論 phantom 標記與否，都重新核對有效病患同意；進畫面和按送出各讀一次。
- null 來源舊紀錄、sample、external 不推定為模擬圖；仍需個案與有效同意。未知來源、無法查證或撤回時拒絕。
- 新驗證版 `1.0-mmh-validation-r2 (26)`，同套件、同 Debug 簽章，使用 install -r 保留原資料。Google 記憶體登入會因程序重啟而失效，需重新登入；不要為啟用按鈕而新增病患、補簽同意或重修遮罩。

本輪 JVM 35/35（同意 9、收據 23、防重複送件 3）與 APK 建置通過，owner_guard 和 diff whitespace 通過。Android Studio JDK25 與 Gradle 不相容，改用專案既有 JDK17；舊 build 下同步產生的重複 `.flat` 檔讀取卡住，保留原檔改以獨立 /private/tmp 輸出後成功。

尚待同筆實機重送與雲端原收據／筆數／資產雜湊驗收，不能以 JVM 通過代替實機結果。證據：`mmh-iap-update-20261011/android-phantom-consent-r2/`。
