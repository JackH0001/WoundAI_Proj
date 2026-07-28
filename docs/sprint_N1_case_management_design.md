# Sprint N1：個案化醫療紀錄管理 — 設計文件

> **為什麼現在做**：n=20 臨床收案的前置。沒有個案綁定，收進來的照片無法追蹤同一傷口的癒合趨勢；
> 沒有真實同意紀錄，日後 IRB 不會認這批資料。個案管理已經從「n=20 之後的 Sprint」變成 **n=20 的前置**。

## 0. 現況（2026-07-28 稽核）

| 項目 | 狀態 | 證據 |
|---|---|---|
| Room `patients` 表、FK、完整 PatientDao | 寫好了但**零呼叫** | `PatientDao.kt:11-39`，全專案只用 `measurementDao()` |
| 量測綁病患 | ❌ `patientId = null` 硬寫 | `MeasureViewModel.kt:310` |
| 傷口時間軸 | 全表撈、不分組 → 兩位病患畫在同一條線 | `WoundTimelineScreen.kt:28` |
| 知情同意 | ❌ `ConsentRecord` 是零引用死碼；`consent_train=true` **硬編碼** | `WoundPipeline.kt:21`、`BackendClient.kt:133` |
| WD-code | ❌ 揮發性 timestamp，不落地，回診拿到新 code | `MeasureValidationEntry.kt:151` |
| 資料庫升級 | ❌ `fallbackToDestructiveMigration()` — **schema 一改清空全部病歷** | `WoundMeasurementDatabase.kt:19` |

**一句話**：schema 有、功能等於零，而且有一顆資料滅失的地雷。

## 1. 核心原則

### 1.1 PII 邊界（最重要）

```
┌──────────── 手機本機（受控、加密） ────────────┐      ┌──── 後端／雲端 ────┐
│  姓名・病歷號  ──加密──►  patients            │      │                    │
│         │                                      │      │                    │
│         └──►  wound_cases.wdCode ──────────────┼─────►│  WD-xxxxxxxx      │
│                （唯一離開本機的識別子）        │      │  影像・遮罩・標註  │
│               consents（簽名 PNG、時間戳）     │      │  **零 PHI**        │
└────────────────────────────────────────────────┘      └────────────────────┘
```

- **PII 永不離開手機**。後端只認 `WD-code`，這在飛輪端已經用 regex 強制（`^WD-[A-Za-z0-9_-]{1,32}$`）。
- 對照表（WD-code ↔ 病患）**只存在本機加密區**，不進雲端、不進版控、不進備份匯出。
- 對應 `IRB_consent_templates.md:29`「姓名/病歷號僅存醫療端、上傳前去識別」、
  `Risk_Management_File_ISO14971.md:12` H6 PHI 外洩風險項。

### 1.2 三層資料模型

現行只有 `Patient → Measurement` 兩層，**缺中間的「傷口」**。一位病患可能同時有薦骨壓瘡與右足潰瘍，
兩者癒合曲線完全不同，混在一起的趨勢圖沒有臨床意義。

```
Patient (1) ──< WoundCase (n) ──< Measurement (n)
   │                  │
   │                  └── wdCode（穩定、唯一、去識別）
   └──< Consent (n)   ← 同意綁「病患」，可涵蓋其名下所有傷口
```

### 1.3 WD-code 必須穩定

一個**傷口個案**一組 code，建立時產生後永不改變。同一傷口第 5 次回診仍是同一個 code
→ 後端 `effective_queue` 的「同影像取最新」與時間軸才串得起來。

格式 `WD-` + 8 碼大寫十六進位（`SecureRandom`），碰撞機率 ~1/4.3e9，且 DB 有 UNIQUE 約束。
**不可再用 `System.currentTimeMillis().takeLast(8)`**——27.8 小時就循環一次。

## 2. 資料模型

### 2.1 `patients`（既有表，加欄位）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String PK | UUID |
| `name` | String | **加密**（Keystore AES-GCM，見 §3） |
| `medicalRecordNumber` | String | **加密** |
| `mrnHash` | String | 病歷號的 **Keystore HMAC-SHA256**。查重／比對用，不可還原。<br>⚠ 不能用「加鹽 SHA-256」：病歷號熵極低而鹽寫在原始碼裡對攻擊者是公開的，字典攻擊幾秒就反查回明文；HMAC 金鑰在 Keystore 不可匯出才擋得住 |
| `birthDate` / `gender` / `department` | String | 準識別資訊，本輪不加密但不外傳 |
| `registrationTime` / `lastVisitTime` / `notes` | — | 既有 |

### 2.2 `wound_cases`（新）

| 欄位 | 說明 |
|---|---|
| `id` Long PK | |
| `patientId` String FK → patients（CASCADE） | |
| `wdCode` String **UNIQUE** | 去識別代碼，唯一會離開本機者 |
| `bodySite` String | 薦骨／右足跟／左小腿… |
| `woundType` String | 壓瘡／糖尿病足／燒燙傷… |
| `onsetDate` Date? | |
| `createdAt` Date / `closedAt` Date? | 個案開立／結案 |
| `notes` String? | |

### 2.3 `consents`（新）

依 `IRB_consent_templates.md:23-26` 的**雙層同意**：

| 欄位 | 說明 |
|---|---|
| `id` Long PK | |
| `patientId` String FK | 同意綁病患（一次簽，涵蓋其名下傷口） |
| `consentCare` Boolean | ①照護必填。false 就不該做任何量測 |
| `consentTrain` Boolean | ②訓練選填，**可撤回**。這才是送後端的 `consent_train` 真值 |
| `signaturePng` ByteArray | 手寫簽名點陣，**以 PhiCrypto 加密後存 BLOB**（簽名是可識別資訊，與姓名同級） |
| `signedAt` Date / `signerRole` String | 本人／法定代理人 |
| `witnessStaff` String? | 見證醫護 |
| `templateVersion` String | 同意書版本（改版後舊同意是否仍有效要看得出來） |
| `withdrawnAt` Date? / `withdrawReason` String? | 撤回留痕，**不刪除原紀錄** |

### 2.4 `measurements`（既有表，加欄位）

| 新增欄位 | 說明 |
|---|---|
| `caseId` Long? （**只建索引、不加外鍵**） | 綁傷口個案。SQLite 無法對既有表 ALTER 加 FK，要加就得整表重建搬資料；這張是真實病歷，重建風險高於好處，參照完整性改由 `CaseRepository` 把關 |
| `wdCode` String? | 送出當下的 code，快照留存 |
| `imageId` String? | **後端影像綁定**，讓本機病歷與雲端飛輪對得起來 |
| `mmPerPx` Double? / `route` String? / `source` String? | 溯源 |

## 3. PII 加密（不新增 Gradle 相依）

用 **Android Keystore 產生的 AES-256-GCM 金鑰**做欄位級加密，由 `CaseRepository` 明確加解密。

⚠ **刻意不用 Room `TypeConverter`**：TypeConverter 依「型別」套用，掛在 `String` 上會把全庫每個字串欄位
（notes、量測的 route…）都加密，完全不是我們要的。代價是**必須經由 Repository 存取**，
直接呼叫 `PatientDao` 會寫進明文 PHI 且不會有任何錯誤——舊的 `data/repository/PatientRepository`
正是這個陷阱，已標為 `DeprecationLevel.ERROR`。

- 金鑰別名 `woundai_phi_v1`，`setUserAuthenticationRequired(false)`（不強制每次解鎖，否則背景寫入會失敗）
- 金鑰**不可匯出**、隨 App 移除而失效 → 手機遺失時資料等同不可讀
- 每筆各自的 12-byte IV 與密文一起存（格式 `IV(12) || ciphertext+tag`，Base64）
- minSdk 24，Keystore AES-GCM 需 API 23+ ✔

**為什麼不用 SQLCipher**：要新增原生相依、增大 APK、且他們的 Gradle/AGP 組合剛穩定下來（wrapper 釘 8.13），
不值得為此再冒建置風險。欄位級加密已覆蓋真正敏感的兩欄；整庫加密列為後續（見 §7）。

**誠實邊界**：欄位級加密擋的是「手機遺失／備份外流／他 App 讀 DB 檔」。
它擋不了「App 執行中被 root 取得記憶體」，也不是 HIPAA/GDPR 的完整技術保護措施。

## 4. 畫面流程（最小可收案）

```
主畫面
  └─ 個案管理
       ├─ 病患清單（顯示別名＋部分遮蔽的病歷號，如 A12***89）
       │    └─ ＋新增病患（姓名／病歷號／生日／性別／科別）
       └─ 病患詳情
            ├─ 知情同意狀態  ← 未簽或已撤回 → 擋住量測入口
            │    └─ 簽同意書：①照護(必)②訓練(選) → 手寫簽名 → 存
            └─ 傷口個案清單（每筆顯示 WD-code、部位、最近面積）
                 ├─ ＋新增傷口個案（部位／類型）→ 產生穩定 wdCode
                 └─ 個案詳情
                      ├─ 量測（沿用現有 SamplePicker/相機 → classify → 修邊）
                      └─ 時間軸（**只畫這個個案**的面積趨勢）
```

### 閘門（Gate）

| 條件 | 行為 |
|---|---|
| 無 `consentCare` | 量測入口停用，顯示「需先取得照護同意」 |
| 無 `consentTrain` | 可量測，但**送訓練標註**的按鈕停用並說明 |
| 已 `withdrawnAt` | 同上，且提示「此病患已撤回訓練同意」 |
| 未選個案 | 量測結果不得存入時間軸（避免又產生 orphan 紀錄） |

## 5. 與既有飛輪的接點（改哪幾行）

| 位置 | 現況 | 改成 |
|---|---|---|
| `BackendClient.kt:133` | `.put("consent_train", true)` 硬編碼 | 由 `ConsentEntity.consentTrain` 帶入 |
| `MeasureValidationEntry.kt:151` | code = timestamp 尾 8 碼 | 用 `woundCase.wdCode` |
| `MeasureViewModel.kt:310` | `patientId = null` | `caseId = 選定個案` |
| `WoundTimelineScreen.kt:28` | `getAllMeasurements()` | `getMeasurementsByCase(caseId)` |
| 撤回同意 | 只有後端 `/consent/withdraw` | 本機同時寫 `withdrawnAt`，並呼叫後端撤回該病患名下所有 wdCode |

## 6. 法規對應

| 要求 | 出處 | 本設計如何滿足 |
|---|---|---|
| 姓名/病歷號僅存醫療端、上傳前去識別 | `IRB_consent_templates.md:29` | §1.1 PII 邊界；後端 regex 強制只收 WD-code |
| 電子簽名＋勾選＋時間戳須系統留存 | 同上 `:37` | `consents` 表（簽名 PNG＋signedAt＋templateVersion） |
| 雙層同意、訓練同意可撤回 | 同上 `:23-26` | `consentCare` / `consentTrain` / `withdrawnAt` |
| 撤回即排除訓練 | 同上 `:58` | 本機留痕 ＋ 後端 code∪image_id 雙鍵排除（已實作） |
| PHI 外洩風險緩解 | `ISO14971 H6` | Keystore 欄位加密；PHI 不進雲端、不進版控 |
| 稽核軌跡不可竄改 | `IEC62304` | 後端 `audit.jsonl` append-only；本機同意撤回不刪原紀錄 |

## 7. 明確不做（本輪範圍外）

- **整庫加密（SQLCipher）**、遠端抹除、螢幕防截圖 — 列為 N2
- **多裝置同步／院內 HIS 介接（FHIR）** — 需要 PHI 出手機，另案評估
- **多使用者/RBAC**：目前是單機單使用者假設。`DoctorAuthActivity` 的硬編碼帳密已被移除成無法登入的字串，
  本輪不修復它（它與個案管理無關，且應改走後端認證）
- **復活 `.kt.disabled` 的舊病患模組**：那些檔壞在與個案無關的 API（OpenCV/GL/Material icon），
  重寫成本低於修復成本，本輪直接寫新的

## 8. 實作順序

1. **P0 先修**：移除 `fallbackToDestructiveMigration()`，補 v1→v2 Migration（資料滅失地雷）
2. `PhiCrypto`（Keystore AES-GCM + HMAC）＋ `CaseRepository` 加解密邊界
3. `WoundCaseEntity` / `ConsentEntity` / DAO；`MeasurementEntity` 加欄位
4. 最小 UI：病患・個案清單/新增、同意勾選＋簽名畫布
5. 接線：consent_train 真值、wdCode 穩定、caseId 綁定、時間軸依個案
6. 回歸：DB migration 測試（v1 有資料 → v2 不掉資料）、加密 round-trip、閘門邏輯

## 9. 收案前檢核（併入 `clinical_pilot_20_SOP.md`）

- [ ] 病患已建檔，同意書已簽（①照護必填）
- [ ] 每個傷口有獨立個案與 WD-code
- [ ] 要進訓練集者，②訓練同意已勾選（否則送出鈕停用）
- [ ] 紙本同意書編號已記在個案 notes（雙軌保險）
