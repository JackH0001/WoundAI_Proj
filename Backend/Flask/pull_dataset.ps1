# 把雲端的組織分割訓練資料拉到本機。
#
# 用法：
#   .\pull_dataset.ps1 -BaseUrl <網址> -Bucket woundai-flywheel-jackh001 -Out D:\woundai_ds
#   .\pull_dataset.ps1 ... -RequireEdited:$false      # 連未經醫師修正的也拉（只為看數量，不可訓練）
#   .\pull_dataset.ps1 ... -Kind wound                # 傷口範圍資料集（不需組織遮罩）
#
# ## 為什麼分成兩段
#
# **控制面**（Cloud Run）：`/api/v1/dataset/manifest` 決定「哪些合格」——套用同意、撤回、
# 誤送排除、醫師修正判準與品質門檻，並把這次匯出寫進稽核軌跡。回應只有幾百 KB。
#
# **資料面**（GCS）：影像與遮罩用 `gcloud storage cp` 直接抓。Cloud Run 的回應有 32MB 上限、
# 會請求逾時，而資料集是 GB 量級；串流大檔還要付 CPU-秒。GCS 本來就有 IAM、可續傳。
#
# ## ⚠ 這個動作會把臨床影像下載到你的電腦
#
# 下載之後，本機那份就**不再受後端的撤回同意與保存期限約束**。
# 病患日後撤回訓練同意時，雲端會自動排除，但你手上這份不會——必須自己刪。
# 這不是可以自動化的事（我們不知道你把它複製到哪裡去了），只能靠流程。
# 見 docs/tissue_segmentation_plan.md 與 docs/pre_collection_checklist.md。
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存。

param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Prefix = "flywheel",
    [string]$Out = "$PSScriptRoot\_dataset",
    [string]$User = "eng01",
    [ValidateSet("tissue", "wound", "interrater")][string]$Kind = "tissue",
    [string]$Source = "clinical",
    [bool]$RequireEdited = $true,
    # 品質門檻。預設值見 docs/tissue_segmentation_plan.md §4.3。
    [double]$MinFocus = 80,
    [double]$MaxClipped = 0.05,
    [double]$MaxSkew = 0.25,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
$BaseUrl = $BaseUrl.TrimEnd('/')
function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }

$GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }

# ⚠ 主體一律編成 UTF-8 位元組陣列再送。PowerShell 5.1 的 Invoke-RestMethod 在
# ContentType 沒帶 charset 時會用 ISO-8859-1，中文變問號且**請求照樣成功**
#（2026-08-05 十組帳號就是這樣壞的，見 docs/admin_operations.md）。
function Invoke-JsonApi {
    param([string]$Uri, [string]$Method = "GET", $Body, [hashtable]$Headers, [int]$TimeoutSec = 120)
    $p = @{ Uri = $Uri; Method = $Method; TimeoutSec = $TimeoutSec }
    if ($Headers) { $p.Headers = $Headers }
    if ($null -ne $Body) {
        $json = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Compress }
        $p.Body = [Text.Encoding]::UTF8.GetBytes($json)
        $p.ContentType = "application/json; charset=utf-8"
    }
    Invoke-RestMethod @p
}

Say "登入 $BaseUrl（需 audit.read 權限：工程師或管理者）"
$sec = Read-Host "密碼（$User，不會顯示）" -AsSecureString
$pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$login = Invoke-JsonApi "$BaseUrl/api/auth/login" -Method POST -Body @{ username = $User; password = $pw }
$H = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "  ✓ $($login.identity)（$($login.role_zh)）"

Say "取得資料集清單"
$q = "kind=$Kind&source=$Source&require_edited=$(if ($RequireEdited) {1} else {0})" +
     "&min_focus=$MinFocus&max_clipped=$MaxClipped&max_skew=$MaxSkew"
$man = Invoke-JsonApi "$BaseUrl/api/v1/dataset/manifest?$q" -Headers $H
Write-Host "  合格 $($man.count) 筆"
if ($null -ne $man.excluded -and $man.excluded.PSObject.Properties.Count -gt 0) {
    Write-Host "  排除："
    $man.excluded.PSObject.Properties | ForEach-Object { Write-Host "    $($_.Name)：$($_.Value)" }
}
if (-not $RequireEdited) {
    Warn "已包含 tissue_edited=false 的樣本。那些遮罩是 AI 色彩啟發式的原樣輸出，沒有人看過——"
    Warn "拿去訓練等於用模型自己的輸出訓練自己。**只可用於清點數量，不可進訓練集。**"
}
if ($man.count -eq 0) { Warn "沒有合格樣本，結束。"; exit 0 }

# 樣本數過少時大聲說出來。50 張以下訓不出有意義的組織分割模型（見規劃文件 §3），
# 而「跑完了、Dice 很低」看起來像模型不好，實際上是資料不夠——這兩者的處置完全不同。
if ($Kind -eq "tissue" -and $man.count -lt 50) {
    Warn "只有 $($man.count) 筆。組織分割在 50 筆以下訓不出有意義的結果（見 docs/tissue_segmentation_plan.md §3）。"
    Warn "建議先用公開資料集（WoundTissueSeg 147 張 / DFUTissue 110 張）建立基線，自有資料作微調。"
}

if ($DryRun) { Say "-DryRun：只看清單，不下載"; exit 0 }

New-Item -ItemType Directory -Path "$Out\images" -Force | Out-Null
New-Item -ItemType Directory -Path "$Out\masks" -Force | Out-Null

if ($Kind -eq "interrater") {
    # 一致性資料：同一張影像有多份遮罩，檔名要帶標註者才分得開。
    Say "下載多標註者影像與各自的遮罩"
    Write-Host "  多標註者影像 $($man.count) 張・可比較配對 $($man.pairs) 組（單一標註者 $($man.single_rater_images) 張）"
    if ($man.count -eq 0) {
        Warn "沒有被兩人以上標過的影像。讓兩位醫師標同一張即可累積——"
        Warn "不需要特地做研究，日常流程中偶爾重複標註就會產生。"
        exit 0
    }
    $n = 0
    foreach ($it in $man.items) {
        & $GCLOUD storage cp "gs://$Bucket/$Prefix/$($it.image_key)" `
            "$Out\images\$($it.image_id).jpg" --quiet 2>$null
        foreach ($r in $it.raters) {
            $safe = $r.actor -replace ":", "_"
            & $GCLOUD storage cp "gs://$Bucket/$Prefix/$($r.tissue_mask_key)" `
                "$Out\masks\$($r.code)__$($it.image_id)__$safe.png" --quiet 2>$null
        }
        $n++
    }
    $man | ConvertTo-Json -Depth 8 | Set-Content "$Out\manifest.json" -Encoding UTF8
    Write-Host "`n✓ $n 張 → $Out" -ForegroundColor Green
    Write-Host "`n下一步： python engineering/phase2/analyze_interrater.py --data `"$Out`"" -ForegroundColor Gray
    exit 0
}

Say "下載影像與遮罩（直接對 GCS，不經過 Cloud Run）"
# 逐檔 cp 而不是整個前綴 rsync：manifest 已經套用了同意與品質規則，
# rsync 會把**被排除的樣本也拉下來**——包含已撤回同意的那些。那是合規事故。
$n = 0; $fail = 0
foreach ($it in $man.items) {
    $img = "gs://$Bucket/$Prefix/$($it.image_key)"
    $dst = "$Out\images\$($it.code)__$($it.image_id).jpg"
    & $GCLOUD storage cp $img $dst --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "  ✗ 影像 $($it.image_id)"; $fail++; continue }
    if ($it.tissue_mask_key) {
        $m = "gs://$Bucket/$Prefix/$($it.tissue_mask_key)"
        $md = "$Out\masks\$($it.code)__$($it.image_id).png"
        & $GCLOUD storage cp $m $md --quiet 2>$null
        if ($LASTEXITCODE -ne 0) { Warn "  ✗ 遮罩 $($it.image_id)"; $fail++; continue }
    }
    $n++
    if ($n % 20 -eq 0) { Write-Host "  …$n / $($man.count)" }
}

# manifest 一定要一起存。沒有它就不知道每張的仿射參數（遮罩柵格 → 影像座標）、
# 品質指標、標註者——而標註者是切分訓練/驗證時避免資料洩漏的關鍵
#（同一位醫師、同一個病患的樣本橫跨兩邊，指標會虛高）。
$man | ConvertTo-Json -Depth 8 | Set-Content "$Out\manifest.json" -Encoding UTF8

Write-Host "`n✓ 下載完成：$n 筆（失敗 $fail）→ $Out" -ForegroundColor Green
Write-Host @"

下一步（本機訓練）：
  python engineering/phase2/train_tissue_seg.py --data "$Out" --epochs 60

⚠ 這份資料現在**不再受後端的撤回同意與保存期限約束**。
   病患日後撤回訓練同意時雲端會自動排除，但你手上這份不會——必須自己刪。
   訓練完請把原始影像刪除，只保留模型權重與指標。
"@ -ForegroundColor Gray
