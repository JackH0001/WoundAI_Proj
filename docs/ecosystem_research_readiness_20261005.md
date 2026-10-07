# WoundAI／WoundLite／WoundAI3D 研究與發布準備度覆核

> 同日後續：隔離開發 App 已通過真 Apple attestation／receipt／assertion，修復三個合成測試未涵蓋的格式／簽章問題；本機 179/179。正式 Lite App ID、production 環境與 GCS 完整串接仍待驗收，見 [實機證據與限制](lite_real_apple_attest_validation_20261005.md)。

核對日期：2026-10-05。Jack 本日確認 **IRB 尚未送件**。本報告是程式與流程覆核及送審準備建議，不是 IRB、TFDA 或 Apple 的核准文件。

> 同日後續：R1 內參漏洞已於候選工作樹修復，深度端點 132 項、變異 10/10 通過，尚未提交／部署。詳見 [修復驗證](medical_depth_calibration_validation_20261005.md)。下方反例表保留為修復前的覆核證據，不代表修復後仍接受錯誤值；其他缺口未因此解除。

## 判定

「WoundAI 臨床精標＋WoundLite 前導採集＋獨立 WoundAI3D 研究」方向可繼續；**尚未達成正式研究收案或 Lite 公開研究收集的完整条件**。模擬資料開發／受控 TestFlight 可繼續，臨床收案及民眾招募先完成適用倫理審查。不能把 App 有同意開關、TestFlight 通過、桶有保留政策或測試通過視為研究核准。

本次未部署、未上傳研究影像、未新增收案、未更改現有雲端權限或刪除資料。原有研究封存仍為隔離資料，不放行訓練。

## 實作、測試與上線的差異

| 路徑 | 已核對的能力 | 仍缺的證據／能力 |
|---|---|---|
| WoundAI 臨床影像與標註 | 醫師角色權限、傷口輪廓、組織標註、同意／撤回及組織匯出測試 | 帳號角色不等於醫師資格查核；需要標註 SOP、類別定義、覆核／仲裁與標註品質驗證 |
| WoundAI LiDAR | 拍摄深度本機側檔、Float32 公尺上傳端點 | **先送標註、另按補傳**；不是每次量測自動完整上雲。內參檢查、跨檔一致性及可核驗回執不足 |
| WoundLite 新候選版 | 同次 RGB／Float32 深度雜湊、不可覆寫封包、簽章請求、回讀回執、去重與撤回測試 | 尚無獨立線上 Lite 服務驗收；App Attest 真實 Apple／實機／GCS 全鏈路仍待驗證 |
| WoundLite 既有資料 | 本日較早封存 16 筆隔離紀錄、50 份唯一內容雜湊通過；含 PNG 深度 | 不是新版原始 RGB-D 入庫證據。封存工具尚未涵蓋 `lite_raw` 封包 |
| WoundAI3D | 經校驗的單幀匯入、可見表面重建、不可變修訂、分組與每次讀取重新確認授權 | 兩種生產來源的轉接器、線上授權供應者、撤回跨封存及訓練工作傳播未接通；本次未訓練模型 |
| WoundAI 發布 | Build 26 本日已在 TestFlight 外部群組顯示「正在測試」 | 不等於正式 App Store／臨床使用核准；本次未確認手機已安裝 26 |
| WoundLite 發布 | 既有 Lite 34 archive、離線候選與單次 Linux 容器建置證據 | 公開服務、研究範圍／責任、隱私聲明及送審資料仍待完成 |

本次 Cloud Run 唯讀實查（`woundai-jackh001`、`asia-east1`）：

- `woundai-backend`：`woundai-backend-00039-xdk`，100% 流量，GCS，maxScale 3。
- `woundai-backend-demo`：`woundai-backend-demo-demo-4005f392-pw2-10020537`，100% 流量，LocalStore，Lite API 關閉，min/maxScale 1。
- 清單只有這兩個服務。不能將本機或 Cloud Build 測試成功寫成新 Lite 服務已部署。demo 的單實例上限不保證資料持久化。

## 收案前要修補的技術缺口

### R1：醫療版可收下無法可靠反投影的內參

`Backend/Flask/api_flywheel.py:661` 的 `validate_depth_payload` 只檢查 fx/fy/cx/cy 是數字，參考尺寸僅在兩欄同為整數時檢查正值。本次直接執行**未修改的驗證器 AST**，用 2×2、每點 0.3 m 的合成深度重現：

| 輸入 | 現行結果 | 應有結果 |
|---|---|---|
| 正常內參、參考尺寸 2×2 | 接受 | 接受 |
| 缺 ref_width/ref_height | 接受 | 拒絕或明列為不可作校準 3D 的舊格式 |
| 參考尺寸為字串 wrong | 接受 | 拒絕 |
| fx=0 | 接受 | 拒絕 |
| fx=NaN | 接受 | 拒絕；此例直接驗證函式邊界，不依賴其他 JSON 層接受非標準數字 |
| 全有效深度但自報 coverage=0.01 | 接受並保留 0.01 | 伺服器重算，不信任自報統計 |

這是研究資料品質缺陷，不代表本次真實裝置每張都傳錯。既有 32 項深度端點檢查仍通過，證明原測試沒有覆蓋以上反例。重現腳本及 JSON 在下列證據目錄；本次沒有將此缺陷誤報為已修復。

修復驗收：有限且正值焦距、有限主點、嚴格正整數參考尺寸、明確單位／位元組序、伺服器統計重算；正常 App 請求保持相容，缺校準的歷史資料只可隔離，不得猜值補成有效樣本。

### R2：醫療版深度仍是人工補傳且跨檔未原子化

`ReviewView.swift:117`、`BackendClient.swift:662`：有 image_id、有本機深度、已送標註且具 annotation.submit 才能補傳；nurse demo 帳號不能驗收醫師標註／深度送出。應用專用模擬醫師測試身分驗收，不升權 Apple 審查帳號。

`api_flywheel.py:1563` 附近依序寫 `.f32`、`.meta.json`、索引。沒有將 RGB、深度、內參與標註版本綁成同一個不可變提交，重傳會覆寫側檔；iOS 只看 `stored`，沒有逐項核對服務端檔案雜湊。跨檔中途失敗／同時補傳的混版風險本次為程式覆核發現，尚未執行真實 GCS 故障注入。

修復驗收：先完成同意及標註資格檢查，再以 capture／revision 自動補送佇列提交；相同內容重試不重收、衝突產生明確修訂，回執可證明哪份 RGB、深度、校準及標註已保存。可見 pending／failed／verified 狀態，不以照片上傳成功代替深度完成。

### R3：把「完整深度」分成三個層次

1. **原始資料保存完整**：擷取到的 RGB、原始深度、原生校準及取得方式均可核對位元組。
2. **單幀可見表面可重建**：額外證明 RGB、遮罩與深度網格、方向、相機內參對齊正確。缺洞／無效區域仍保留，不補成真值。
3. **多視角可用**：同一傷口、同一擷取 session，多幀時間與實際相機姿態、座標慣例及追蹤品質齊備，且通過配準驗證；不等於已得到完整閉合傷口或可信體積。

Lite 的 `LiteRawDepthPacket.swift:51` 明確宣告 `confidence_kind=validity_only`、`pose=unavailable`、`capture_time=unavailable`、`registration=not_verified`。這是誠實的可用性描述，不能升格為原生 sensor confidence 或已對齊多視角。WoundAI 的側檔 captured_at 是序列化時 Date()，不是已證明的感測器曝光時間；上傳轉換未攜帶所有原始方向欄位。

**單幀重建不必有世界座標姿態**；本階段先補齊第 1、2 層，符合目前表面積／前導資料目標。若要預留多視角，必須在拍攝當下收集姿態與同步資訊，不能日後從兩張不同時間的照片或面積值補出。

WoundAI3D 最新工作樹另有 NativeCapturePacket／DiagnosticMultiView，可處理本機 ARKit 原生 confidence、session 姿態與局部表面疊合，但標為 local_sensor_diagnostic、sensor_reported_not_independently_verified。它不是兩個前導 App 已上線的雲端資料流；本次 52 項測試涵蓋研究匯入／資料集分組，未另宣稱此診斷多視角鏈已實機驗收。

### R4：雲端、封存、WoundAI3D 尚未形成可撤回的訓練資料鏈

每日工具 `tools/lite_research_archive.py:97` 只枚舉 jpg/json/depth.png/conf.png，且明確寫 raw_rgbd_present=false。不能以每天成功備份，推論新原始 RGB-D 也有備份。

WoundAI3D 的 ResearchCaptureImporter 已要求資產雜湊、RGB 尺寸、明確校準參考及 5 分鐘內的授權；ResearchDatasetStore 會排除撤回／過期授權、舊修訂及 screen/phantom/synthetic 研究訓練樣本。但目前授權由可信呼叫端傳入，還不是線上簽發者／撤回服務；現有 importer 的標註資產是二元傷口遮罩，沒有醫療組織分類層的完整匯入契約。

修復驗收：兩來源的版本化轉接器、組織類別表／遮罩映射、raw 封包封存、短期授權來源及撤回索引；從雲端撤回後，各副本／資料集／下次訓練讀取均失效。每日封存是最終一致，不能承諾即時清除所有副本或已訓練模型反學習。30 天／365 天是目前成本覆核候選，不取代核准研究保存期限；第二份受控備份及退役協定完成前不刪研究雲端來源。

### R5：去識別化與備份仍需實際驗收

同日後續：Lite 主索引及共用影像／深度目錄已加入備份排除與原子寫入保護；Lite 149 通過＋1 模擬器不支援項改由實機驗證、醫療 79/79、獨立 iPhone 合成測試 7/7。尚未發布，醫療 SQLite/WAL、其他匯出與 Mac 備份仍未閉合；詳見 [驗證紀錄](lite_backup_protection_validation_20261005.md)。下方為修復前盤點。

現行 Lite 人臉檢查是正面人臉偵測後拒收，不是可靠的人臉模糊化，也不涵蓋證件文字、刺青、背景、私密部位等所有識別風險。可跨時間連結同一安裝／傷口的代碼應稱假名化或去識別化處理，不能保證完全匿名。身體部位／組織研究標註應保留，移除的是可識別個人的標籤，兩者不同。

LiteStore、WoundStore、LocalImageStore 主資料路徑未找到明確排除系統備份；LocalImageStore 註解更預期密文可進備份。加密不是排除 iCloud 的證據，App Attest／撤回日誌單獨排除也不涵蓋整個資料庫。應盤點兩 App 的照片、深度、SQLite/WAL、JSON、匯出 PDF、Mac vault、Time Machine 與其他同步位置，驗證備份排除／受控備援及撤回政策。本次沒有讀取使用者 iCloud 內容，未宣稱已有個資外洩。

## 法規與審查：本次可確認的界線

- **IRB**：人体研究原則上需在實施前完成審查，受試者同意方式依核准計畫。保存期限、後續用途與商業利益須納入告知；目前尚未送件，故不能寫成已獲准收集 WoundAI3D 訓練材料。[人體研究法第 5、12、14、19 條](https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0020176)
- **簡易審查不等於自行認定低風險**：公告有風險門檻及案件類型限制，影像的可辨識性、隱私風險與使用器材條件都相關。應由接件 IRB 判斷能否簡易審查；不能只因非侵入式拍照就保證適用。[衛福部簡易程序範圍](https://dep.mohw.gov.tw/DOMA/dl-16069-71ed79fe-8929-4a85-91b2-56cbafefeb0a.html)
- **個資**：醫療相關可直接或間接識別資料受個資法規範，去姓名不會自動排除適用；應確認蒐集依據、告知範圍及撤回／刪除處理。[個資法第 2、6、8 條](https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021)
- **Apple**：健康人體研究需同意與獨立倫理核准；敏感資料服務涉及法人提交、資料最少化、用途、撤回及個人健康資訊不得存 iCloud 等要求。量測準確度聲明需證據。待核對團隊法律實體，不能由帳號顯示姓名推定類型。[審查規範 1.4.1、5.1.1、5.1.3](https://developer.apple.com/app-store/review/guidelines/tw/)
- **TFDA**：是否為醫療器材軟體依功能、用途、使用方法與原理綜合判斷，非醫療免責文字不能單獨決定分類。醫療版有組織／嚴重度等功能，Lite 亦須提交實際預期用途作屬性評估；IRB 不取代器材許可。[TFDA 醫療器材軟體 Q&A](https://www.fda.gov.tw/tc/includes/GetFile.ashx?id=f637559984474447747&type=1)

上述為台灣與 Apple 的準備度核對，不是全球上市合規結論。先選定成人、地區、機構與招募來源，再作各上市地規範評估。

### 免費辨識與研究同意綁定

Jack 先前希望保留研究參與換取免費 AI 功能，本次未擅自更改產品選項。但研究使用與本次推論在目的上不同，必須清楚揭露、由 IRB 評估自願性及利益安排；有離線替代功能並不自動保證綁定可被接受。Apple 對非必要資料取用與付費功能另有限制，不能把未來付費辨識仍要求捐研究資料當成已核准方案。[Apple 5.1.1(ii)、(iv)](https://developer.apple.com/app-store/review/guidelines/tw/)

## 建議送審範圍與資料規格

這是待 PI／機構確認的計畫建議，不代填核准編號或風險分級：

| 研究分支 | 應明列內容 |
|---|---|
| A：WoundAI 臨床隊列 | 常規照護之外的研究拍攝／操作、RGB、校正、輪廓與組織精標、雙人覆核／仲裁、LiDAR 選配、參考量測；研究結果是否影響照護必須明定 |
| B：WoundLite 民眾隊列 | 自主招募資格、裝置與地區、遠端同意、免費辨識安排、原始 RGB-D、使用者修改屬非專業標註、撤回與客服；不要假設 A 的核准自動涵蓋 B |
| 共用後續研究 | 指名 WoundAI3D 的分割／表面建模／驗證用途、受控共享方、保存地點／期限、商業可能性、研究發表、未來目的變更的再審與再同意 |

可先完成 A，再由同一機構決定 B 是另案或明列子計畫，不用為等待全球招募而延後所有模擬測試。

共同 capture 最小規格：來源 App／版本、研究與同意版本、來源類型 physical/screen/phantom/synthetic、假名傷口及 session、實際拍攝時間與時鐘定義、同份 RGB 雜湊、深度位元組／單位／尺寸、校準參考／方向／映射、有效區域與 confidence 的真正來源、原始與修訂遮罩版本／模型版本、醫療組織類別與覆核狀態、儲存回執、當前撤回狀態。世界姿態及追蹤狀態只在確實取得時附上，不能填造。

**臨床精標、一般使用者修正、AI 預測及模擬驗證分層保存**。不能把「開過修正畫面但沒修改」當人工精標，也不能把拍螢幕取得的 LiDAR 深度當真傷口形狀。既有 10.08 cm² 標準物、43°／11.22 cm² 那次約 +11.31%（依畫面四捨五入數值）僅為單次端到端觀察，不足以建立所有角度／裝置的誤差承諾；未實際修改遮罩不能歸責手動修壞。

資料分組需防同一受試者／同傷口洩漏到訓練與測試兩側。安裝代碼不等於跨 App／重裝後同一受試者識別；在不重新識別個人的前提下，研究計畫需定義其限制及去重方式。不存在「95%」通用醫療級門檻，应分別定義分割、面積誤差、組織一致性、重建成功率及信賴區間。

## 本次測試證據

工作樹以 d7e36175020f041bef39b130f811862613902180 為基底，含大量既有未提交修改；**不是僅測該 commit，也不是遠端 CI**。具體來源雜湊、命令、輸出保存在 repo 外證據目錄 `woundai-competitive-review-20261003/ecosystem-audit-20261005/`。

| 驗證 | 實際結果 | 證明範圍 |
|---|---|---|
| 醫療 depth_endpoint／depth_chain／tissue_dataset／tissue_export | 4 個檔案 rc=0，分別 32／34／41／22 個 PASS 檢查 | 合成資料、深度與組織匯出／門檻；不是臨床精度 |
| endpoint_guards | pytest 8/8，無 skipped | 端點權限／退休路由；初次直接執行檔案未收集測試，已作廢並改用 pytest |
| Lite raw_contract／raw_store／raw_HTTP／fenced_HTTP／revision／withdrawal_readers／face_privacy／service_profile | 8 個檔案，113/113 unittest | 本機 fixture／模擬儲存與授權，不是線上 Apple/GCS 整合 |
| 醫療額外反例 | 正常控制通過；5 種錯誤條件仍接受 | R1 已重現，未修復 |
| WoundAI3D ResearchCaptureImporterTests／ResearchDatasetPartitionTests | 52/52，0 failures | 研究匯入、資料完整性、撤回／修訂與分組；不是實體 LiDAR 精度 |
| Cloud Run | 兩服務模式及流量讀回成功 | 服務配置，不是影像／研究資料內容盤點 |

WoundAI3D 首次被本機編譯快取沙箱阻擋；使用目前預設 swiftbuild 又遇到測試產物 codesign 的 resource fork 錯誤。改用獨立 scratch 目錄及 native build system 成功執行 52 項。沒有修改另一 repo 原始碼、關掉驗證斷言或把失敗當成功；swiftbuild 建置環境問題仍留在原始 log。

重現：使用既有 review venv 執行證據目錄的 `run_checks.py`（雲端路由／憑證隔離、各測試獨立行程）；3D 用 `swift test --build-system native --disable-sandbox --scratch-path <獨立輸出> --filter 'ResearchCaptureImporterTests|ResearchDatasetPartitionTests'`。原始測試 log、`test-results.json`、`medical-validator-counterexamples.json`、`cloud-run-readback.json` 均保留。

## 下一階段完成條件

1. 先修 R1，補反例回歸；為 R2 設計可恢復、去重且綁定版本的 RGB-D 提交，不擴大正式服務權限。
2. 接通 Lite 真實 App Attest＋專用候選儲存，用合成物件做拍攝→上傳→回讀→修改→重試→撤回→封存排除驗收；之後醫療版同樣驗收。
3. 補兩來源轉接器與組織層，將同一合成 capture 匯入 WoundAI3D，核對單位、方向、遮罩位置、雜湊、撤回與舊版排除；不用實際訓練來代替資料契約驗證。
4. 修備份排除、資料分類與隱私文字；備妥 A／B 隊列及後續 WoundAI3D 用途交 PI/IRB，同時確認 TFDA 屬性與提交法律實體。
5. 核准範圍、正式持久化／稽核隔離、實機品質證據與公開政策一致後，才開放相應研究招募／公開發布。測試資料維持隔離，不能事後因上架通過就改列研究訓練資料。
