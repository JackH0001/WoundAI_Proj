# 共用 KEK 退休前準備：分批換包，不是銷毀執行器

2026-11-11；基底 `15fcef1b2273c4dfb589437565fc41fd6a8cc130`。本輪保持本機、draft，沒有部署、IAM、真實金鑰、研究原檔或 Apple 操作。

## 修正前後

既有 `RotatingGrantCipher.rewrap()` 能處理單帳號換包與重送，但沒有跨帳號的有界執行紀錄；舊 write-version 的程序仍能替新帳號包裝 DEK。這不是完整刪帳：舊 DEK 副本仍可透過舊 KEK 解密。

新增 `SharedKEKPreparation`，明確啟用、固定 manifest hash／來源 commit／scope／來源與目標版本、兩組不同 Ed25519 公鑰。每批最多 50 帳號、每個指令最多 5 帳號、最多 32 次明確派送，方案窗口至多一小時、單次簽章至多五分鐘。這些限制只約束操作工具，不限制服務帳號總數；不同批次可使用同一 rotation ID，名單外帳號仍能正常登入。

每個帳號綁定原始 key-row hash。先檢查整批，再建立來源版本的不可逆應用寫入阻擋紀錄；不是 KMS disable。逐帳號沿用既有換包程式，保持 DEK、Apple envelope、研究索引與額度不變。關閉帳號只能在原 closure receipt、proposal tombstone 均可驗證後跳過，不能復活。

派送使用 CAS 取得唯一執行權，記錄命令、檢查點與 hash-only 回執。未確認的派送不能直接重跑：操作者須提交新指令，另一組 drain 簽章須綁定目前檢查點及此次續接。此簽章只證明「這個派送已停止」，不是全域寫入／備份清冊證明。角色由不同密碼學金鑰區分；測試並不證明真實保管人已分開。

## 必須保持的限制

- `complete` 只代表**選定批次**當時完成。`complete_inventory_verified`、`all_writers_drained`、`kms_destruction_authorized`、`account_deleted` 都固定為 false。
- 程式沒有 destroy／disable／restore／create 的 provider、HTTP route、啟動接線或預設啟用。現有 shared provider 仍拒絕 shared-key destroy。
- 新阻擋檢查加入初始化與換包的持久化邊界；一般既有帳號解密不增加此檢查。舊版本仍可讀，以便遷移及復原。
- 檢查不是跨資料列的原子鎖。負向測試刻意在檢查後、CAS 前建立 fence，仍可有一筆 V1 晚到落地。未升級的程序也不識別新 fence。故必須另有停用所有舊程序／權限的真實排空證據，以及排空後的全域盤點，才能進一步考慮銷毀。
- 阻擋紀錄不自動撤銷，失敗不把帳號換回舊版本。目標不可用時，尚未建立 fence 的新批次停止；fence 已建立後新帳號可能暫停，既有資料保留。只能修復目標服務後續接，不承諾可退回任意舊程式。
- 未提交的 initial proposal 仍保留原物件，若依賴已阻擋的版本便停止，不能偷偷丟棄後產生另一把 DEK。該情況需要另行盤點／處置。
- 指令 budget 或窗口耗盡、來源變更、稽核缺失、首次派送 marker 已有但 checkpoint 遺失，都停止，不重設預算。遺失 checkpoint 的修復不在本輪範圍。
- 沒有把 manifest 的 50 個帳號當完整名冊。歷史 GCS generations、soft-delete、備份、其他 scope、未提交 proposals、其他程式版本皆未自動盤點。

## 驗收方式

`engineering/phase2/test_lite_shared_kek_preparation.py` 使用原服務的 Apple grant／AccountLifecycle、實際 AES-GCM 與 SQLite CAS；Apple、KMS REST 是合成 transport，網路禁止。涵蓋：多批次、50 帳號上限、名單外新帳號、兩執行者競爭、錯誤簽章／過期／額度上限、失去派送回覆、已提交 key 後遺失回執、刪帳競爭、舊 proposal、晚到寫入反例及稽核缺漏。

`tools/lite_shared_kek_preparation_probe.py` 用三帳號重現一個關帳、兩個換包。中斷後由新 executor 與獨立 drain 簽章續接，只做兩次換包；停用合成 V1 後兩個存續帳號仍能解密。獨立 AES 解密刻意繞過 App，證明關閉帳號的舊備份**仍可解開**，沒有因 UI 拒絕存取而誤報密碼學銷毀。

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2:. python -B -m unittest -v test_lite_shared_kek_preparation
PYTHONPATH=Backend/Flask:tools:engineering/phase2:. python -B tools/lite_shared_kek_preparation_probe.py --output /tmp/shared-kek-preparation.json
```

測試依賴沿用 repo 的 commerce test requirements。`lite-prelaunch` 增加上述兩項；未推送的新來源不可宣稱 GitHub CI 已通過。App／iOS 本輪無變更，先前 iOS 通過數不可算本輪重跑。

## 下一階段與參照

仍須完成：實際全域依賴清冊、舊程序排空與舊寫入權限封閉、存續帳號及災復副本驗證、不可用期間復原、授權的 KMS 退休執行器、供應者狀態讀回、固定銷毀時間政策。不能單憑本批回執要求 destroy。保留既有原始資料與稽核；本輪不更動任何真實資料。

Google 明確說明，輪替不會自動重新加密既有資料，也不會自動停用或銷毀舊版本：[Rotate a key](https://docs.cloud.google.com/kms/docs/rotate-key)。

`DISABLED` 可重新啟用，`DESTROY_SCHEDULED` 在期限內可復原；`DESTROYED` 才是服務中無法再使用的階段。Google 並另外描述銷毀後備份系統的清除時程，因此「客戶無法透過 KMS 解密」和「所有實體備份已清除」不能混稱：[Key version states](https://docs.cloud.google.com/kms/docs/key-states)。沒有在本輪設定 30／60／90 天的對外承諾。

Claude 請優先檢查：批次清冊是否被誤當全域清冊；命令回覆遺失是否可能雙執行；partial rewrap 後仍能沿用原 proposal；closure 是否復活；不原子 fence 的負向反例是否保留；新模組未被任何公開 route 或 startup 自動啟用。
