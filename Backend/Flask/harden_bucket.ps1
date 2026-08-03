# 收案前的儲存桶強化 —— 在放入真實病人影像之前執行。
#
# 用法：
#   .\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001
#   .\harden_bucket.ps1 -ProjectId woundai-jackh001 -Bucket woundai-flywheel-jackh001 -Audit
#
# -Audit 會另外建立一個**獨立的稽核桶**並設定保留政策（見 §4，有不可逆的選項，預設不鎖）。
#
# ⚠ 本檔必須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則會以 Big5 解讀而語法全爛）。

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Region = "asia-east1",
    [int]$QuarantineDays = 30,
    [switch]$Audit,
    [switch]$LockRetention
)

$ErrorActionPreference = "Continue"
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }
function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }
function Invoke-GCloud { & $script:GCLOUD @args }

& $script:GCLOUD config set project $ProjectId | Out-Null

# ── 1. 封鎖公開存取 ─────────────────────────────────────────────────
# 「不小心把桶設成公開」是雲端資料外洩最常見的單一原因。
# publicAccessPrevention=enforced 是組織層級的硬性阻擋：即使有人之後手滑
# 授予 allUsers，那個 IAM 綁定也會被拒絕，不是「生效但沒人發現」。
Say "封鎖公開存取"
Invoke-GCloud storage buckets update "gs://$Bucket" --public-access-prevention
if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ publicAccessPrevention = enforced" } else { Warn "設定失敗" }

# 統一存取控管：關掉物件層級 ACL。混用 ACL 與 IAM 兩套權限模型，
# 是「以為關了其實還開著」最常見的來源——你查 IAM 看起來乾淨，實際權限在 ACL 上。
Invoke-GCloud storage buckets update "gs://$Bucket" --uniform-bucket-level-access | Out-Null
Write-Host "  ✓ uniformBucketLevelAccess = true"

# ── 2. 物件版本控制 ─────────────────────────────────────────────────
# 誤刪的保險。但它同時也意味著「刪掉的物件其實還在」——
# 所以下面的生命週期規則必須連舊版本一起清，否則撤回同意的影像會以舊版本形式留存，
# 那等於承諾了下架卻沒有真的下架。
Say "開啟物件版本控制"
Invoke-GCloud storage buckets update "gs://$Bucket" --versioning
if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ versioning = enabled" } else { Warn "設定失敗" }

# ── 3. 生命週期規則 ─────────────────────────────────────────────────
Say "設定生命週期規則"
# ⚠ **刻意不對 images/ 設定「N 天後刪除」的規則。**
#
# IRB 承諾的是「結案逾 90 天銷毀」，而「結案」是臨床事件，雲端不知道。
# 用單純的年齡規則會刪掉仍在追蹤中的傷口影像——那是病歷滅失，比留著更嚴重。
# 影像的刪除由 App 端（`CaseRepository.purgeExpiredImages`）與撤回同意流程驅動。
#
# 這裡只設兩條**確定安全**的規則：
#   a) quarantine/ 的物件逾期刪除 —— 那是已撤回同意的影像，保留只為短期稽核，
#      逾期不刪就違背了「撤回即下架/銷毀」的承諾。
#   b) 非最新版本逾期刪除 —— 否則版本控制會讓「已刪除」的物件實際上永遠留著。
$lc = @"
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": $QuarantineDays,
          "matchesPrefix": ["flywheel/quarantine/"]
        }
      },
      {
        "action": {"type": "Delete"},
        "condition": {"daysSinceNoncurrentTime": $QuarantineDays, "isLive": false}
      }
    ]
  }
}
"@
$lcFile = [IO.Path]::GetTempFileName()
try {
    [IO.File]::WriteAllText($lcFile, $lc, (New-Object Text.UTF8Encoding $false))
    Invoke-GCloud storage buckets update "gs://$Bucket" --lifecycle-file=$lcFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ quarantine/ 逾 $QuarantineDays 天自動銷毀（撤回同意的影像）"
        Write-Host "  ✓ 非最新版本逾 $QuarantineDays 天清除（避免版本控制讓刪除失效）"
        Write-Host "  ℹ images/ **未**設年齡規則 —— 刪除由臨床事件驅動，不由日曆驅動"
    } else { Warn "生命週期設定失敗" }
} finally { Remove-Item $lcFile -Force -ErrorAction SilentlyContinue }

# ── 4. 稽核桶（WORM）────────────────────────────────────────────────
if ($Audit) {
    $auditBucket = "$Bucket-audit"
    Say "建立稽核專用桶 gs://$auditBucket"
    # 為什麼要**獨立的桶**：GCS 的保留政策是桶層級，不能只套在某個前綴上。
    # 把保留政策套在主桶會連影像一起鎖住 —— 而影像必須刪得掉（撤回同意、保存期限）。
    # 稽核軌跡則相反：它必須刪不掉。兩種需求相反，所以要分桶。
    Invoke-GCloud storage buckets create "gs://$auditBucket" --location=$Region --uniform-bucket-level-access 2>$null
    Invoke-GCloud storage buckets update "gs://$auditBucket" --public-access-prevention | Out-Null

    # 保留 7 年：醫療紀錄常見的法定保存年限。**未鎖定時可調降或移除**。
    Invoke-GCloud storage buckets update "gs://$auditBucket" --retention-period=7y
    if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 保留政策 7 年（尚未鎖定，可調整）" } else { Warn "保留政策設定失敗" }

    if ($LockRetention) {
        Warn "═══════════════════════════════════════════════════════"
        Warn " 即將**鎖定**保留政策。這個動作不可逆："
        Warn "   · 保留期內任何人都無法刪除物件——包含專案擁有者與 Google"
        Warn "   · 桶本身在保留期滿之前也無法刪除"
        Warn "   · 保留期只能延長，不能縮短"
        Warn " 誤寫進去的東西會在裡面待滿 7 年。**先在測試桶演練過再對正式桶執行。**"
        Warn "═══════════════════════════════════════════════════════"
        $c = Read-Host "確定要鎖定嗎？輸入 LOCK 以繼續"
        if ($c -ceq "LOCK") {
            Invoke-GCloud storage buckets update "gs://$auditBucket" --lock-retention-period
            if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 保留政策已鎖定（不可逆）" }
        } else { Write-Host "  已取消鎖定。" }
    } else {
        Write-Host "  ℹ 未鎖定。試營運期間建議先不鎖，正式收案前再評估 -LockRetention。"
    }
}

# ── 5. 現況檢核 ─────────────────────────────────────────────────────
Say "現況檢核"
$cfg = Invoke-GCloud storage buckets describe "gs://$Bucket" --format=json | ConvertFrom-Json

# ⚠ 欄位名稱有**兩種形態**，取決於你用哪個工具問：
#   `gcloud storage buckets describe --format=json` → snake_case
#       public_access_prevention / uniform_bucket_level_access / versioning_enabled / lifecycle_config
#   REST API 與舊的 gsutil            → camelCase 且多一層巢狀
#       iamConfiguration.publicAccessPrevention / versioning.enabled / lifecycle
# 只寫其中一種的話，設定明明成功卻整排顯示 ✗——**檢核比它要檢查的東西更容易出錯**，
# 而一個會誤報失敗的檢核，下一次就會被當成雜訊忽略掉。兩種都試。
function Get-Field($obj, [string[]]$paths) {
    foreach ($p in $paths) {
        $cur = $obj
        foreach ($seg in $p.Split('.')) {
            if ($null -eq $cur) { break }
            $cur = $cur.PSObject.Properties[$seg].Value
        }
        if ($null -ne $cur) { return $cur }
    }
    return $null
}
function Write-CheckLine($name, $ok, $actual) {
    $mark = if ($ok) { "✓" } else { "✗" }
    $color = if ($ok) { "Gray" } else { "Yellow" }
    $shown = if ($null -eq $actual -or "$actual" -eq "") { "(讀不到)" } else { $actual }
    Write-Host ("  {0} {1,-28} {2}" -f $mark, $name, $shown) -ForegroundColor $color
}

$pap = Get-Field $cfg @("public_access_prevention", "iamConfiguration.publicAccessPrevention")
$ubla = Get-Field $cfg @("uniform_bucket_level_access", "iamConfiguration.uniformBucketLevelAccess.enabled")
$ver = Get-Field $cfg @("versioning_enabled", "versioning.enabled")
$lcRules = Get-Field $cfg @("lifecycle_config.rule", "lifecycle.rule")

Write-CheckLine "位置（須為境內）" ($cfg.location -eq $Region.ToUpper()) $cfg.location
Write-CheckLine "公開存取封鎖" ($pap -eq "enforced") $pap
Write-CheckLine "統一存取控管" ($ubla -eq $true) $ubla
Write-CheckLine "版本控制" ($ver -eq $true) $ver
Write-CheckLine "生命週期規則" ($null -ne $lcRules -and @($lcRules).Count -gt 0) ("$(@($lcRules).Count) 條")
Write-CheckLine "預設加密" $true "Google 管理金鑰（CMEK 見文件 §6）"

Say "是否有公開授權（allUsers / allAuthenticatedUsers）"
$iam = Invoke-GCloud storage buckets get-iam-policy "gs://$Bucket" --format=json | ConvertFrom-Json
$pub = @($iam.bindings | Where-Object { $_.members -contains "allUsers" -or $_.members -contains "allAuthenticatedUsers" })
if ($pub.Count -gt 0) { Warn "❌ 發現公開授權：$($pub.role -join ', ')" }
else { Write-Host "  ✓ 沒有公開授權" }

Write-Host "`n完成。仍需人工處理的項目見 docs/pre_collection_checklist.md。" -ForegroundColor Green
