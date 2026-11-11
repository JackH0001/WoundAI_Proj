# WoundAILite 本人內測的營運金鑰備份與還原

服務使用身分 HMAC，將同一 Apple 使用者連回原帳號、購買關聯及額度。服務復原時必須沿用同一把 HMAC 和原 namespace。這個離線工具把身分材料、研究簽章及操作簽章放入加密備份，另存核對收據。可用 Python 的 `cryptography` 函式庫在 Mac 或 Linux 執行。

| 用途 | 還原出的材料 | 掛載位置或使用者 |
| --- | --- | --- |
| 帳號身分 | 32 bytes HMAC | 個人服務的身分橋接 |
| 研究關聯 | 個人、研究服務各有一把 Ed25519 私鑰 | 各自的服務只接收自己的 issuer 設定 |
| 首次啟用 | 操作員、觀測者各有一把 Ed25519 私鑰 | 操作端簽署指令與獨立觀測。服務只持公鑰 |
| 私有刪帳處置 | 處置操作員、寫入停止確認者各有一把 Ed25519 私鑰 | 私有執行器只持這兩把公鑰 |

這七個角色使用不同材料。備份與復原密語合在一起，持有人即可還原全部七項。本人內測採此保管設計。正式環境的雙人取用、分離保管與災復演練需另行核定。Apple 登入與購買的兩把 `.p8`、帳號資料加密用的共用 KMS 版本均依各自的保管程序處理。

11/11 已增加[每角色獨立加密匯出與還原](lite_role_custody_20261111.md)，從既有七角色備份沿用原金鑰，初始化簽署可只解開操作員角色。合成測試已通過，但原整包備份仍保留，實際保管人分離、整包備援存取管制與災復交接尚未完成，不能視為已達雙人保管。

## 首次建立

1. 先以核定的首次安裝清冊確認身分範圍，已啟用的帳號服務應走還原流程。把核定的 `project`、`environment`、`namespace`、`audience`、`issuer`、`media_bucket` 存成 JSON 物件。建立入口限 `owner-sandbox`，issuer 為 `https://appleid.apple.com`。
2. 在原始碼目錄外，為備份及收據各準備一個自己的目錄，權限設為 `0700`。兩個檔名均須是新的絕對路徑。選擇能獨立保管收據的位置，並另行保管復原密語。
3. 在本人終端執行下列命令，核對顯示的環境與 namespace，再輸入建立確認及復原密語。路徑以本人核定的位置替換。工具會先寫入收據並讀回，再寫入備份並重新讀回、解密及比對。完成後的 `verified: true` 表示這一對本機檔案已通過核對。

```sh
python3 tools/lite_operational_materials.py create-new \
  --scope /absolute/path/to/approved-scope.json \
  --output /absolute/private-backup/materials.json \
  --receipt /absolute/independent-receipts/materials-receipt.json
```

4. 將加密備份複製到核定的備援位置，保留原收據。在新的終端程序對該份副本執行下列核對。也可用先前獨立保存的 `--expected-manifest-sha256` 核對身分指紋。使用收據時會一併核對整份密文的雜湊和大小。

```sh
python3 tools/lite_operational_materials.py verify \
  --backup /absolute/restored-copy/materials.json \
  --receipt /absolute/independent-receipts/materials-receipt.json
```

## 中斷與設定交接

| 看到的狀態 | 後續處理 |
| --- | --- |
| 僅有收據，找不到完整備份 | 保留收據，查找與其雜湊相符的副本。既有服務須找回原材料。對確定尚未啟用的首次安裝，可另行核准新的檔案組 |
| 兩檔都在，終端沒有完成訊息 | 對原檔執行 `verify`，成功才採用 |
| 密語、範圍、角色、雜湊或備份大小不符 | 停止並保留兩檔，從獨立保管位置核對收據與密語 |
| 還原五角色的早期備份 | 保留原身分及簽章。完整處置設定另需兩把獨立的處置簽章 |

新的 `configuration_pins` 含原生服務的 HMAC 指紋與啟用驗證公鑰、兩個 issuer 檔案的指紋、私有處置使用的帳號範圍和驗證公鑰。這些欄位供部署前核對，產出它們不會改變服務或雲端權限。每一把服務用材料的實際匯入、IAM、掛載與復原仍需逐項讀回驗證。

整理好候選服務設定後，可用下列唯讀命令比對。兩個 issuer 指紋應來自各自的服務設定。它會從備份的公開清冊與獨立收據取出預期值，核對服務範圍及金鑰用途，過程不需輸入密語。

```sh
python3 tools/lite_operational_materials.py check-config \
  --backup /absolute/private-backup/materials.json \
  --receipt /absolute/independent-receipts/materials-receipt.json \
  --native /absolute/config/native.json --disposition /absolute/config/disposition.json \
  --native-issuer-sha256 NATIVE_ISSUER_MOUNT_SHA256 \
  --research-issuer-sha256 RESEARCH_ISSUER_MOUNT_SHA256
```

備份以固定 scrypt 參數從復原密語導出金鑰，使用 AES-256-GCM 加密，並將公開清冊綁入驗證。每次封存使用新的隨機 salt 和 nonce。備份密語只由可隱藏輸入的互動終端讀取。[AESGCM 說明](https://cryptography.io/en/stable/hazmat/primitives/aead/) · [Scrypt 說明](https://cryptography.io/en/stable/hazmat/primitives/key-derivation-functions/#scrypt)

合成驗收可執行 `python -m unittest -v test_lite_operational_materials`，以及 `python tools/lite_operational_materials_probe.py --output new-evidence.json`。前者包含簽署登入、購買關聯及 SQLite 額度在復原後的比對。後者使用五個獨立終端程序檢查建立、還原、錯密語、重複建立與遭修改的備份。測試的 PYTHONPATH 包含 `tools`、`engineering/phase2`、`Backend/Flask`。
