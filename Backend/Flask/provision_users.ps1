# 開通臨床測試者帳號（RBAC S1）。
#
# 用法：
#   .\provision_users.ps1 -BaseUrl https://woundai-backend-421209514056.asia-east1.run.app
#   .\provision_users.ps1 -BaseUrl <網址> -Csv users.csv     # 自訂名單
#   .\provision_users.ps1 -BaseUrl <網址> -List              # 只列出現有帳號
#
# 預設開通 10 組臨床帳號（見 $DEFAULT_USERS）。密碼隨機產生並輸出成 CSV，
# **只會顯示這一次**——後端只存 PBKDF2 雜湊，取不回明文。
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存。

param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [string]$AdminUser = "admin",
    [string]$Csv,
    [switch]$List
)

$ErrorActionPreference = "Continue"
$BaseUrl = $BaseUrl.TrimEnd('/')
function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }

# 預設名單。角色比例反映真實傷口照護團隊：以護理師為主力執行量測，
# 醫師負責 GT 確認（人數少但不可或缺），助理協助拍攝。
$DEFAULT_USERS = @(
    @{ user = "dr.chen";    role = "physician"; name = "陳醫師" }
    @{ user = "dr.lin";     role = "physician"; name = "林醫師" }
    @{ user = "dr.wang";    role = "physician"; name = "王醫師" }
    @{ user = "ns.huang";   role = "nurse";     name = "黃護理師" }
    @{ user = "ns.liu";     role = "nurse";     name = "劉護理師" }
    @{ user = "ns.tsai";    role = "nurse";     name = "蔡護理師" }
    @{ user = "ns.yang";    role = "nurse";     name = "楊護理師" }
    @{ user = "as.wu";      role = "assistant"; name = "吳助理" }
    @{ user = "as.hsu";     role = "assistant"; name = "許助理" }
    @{ user = "eng.dev1";   role = "engineer";  name = "工程師（除錯用）" }
)

function New-Password {
    # 去掉 l/1/I、O/0 這些看起來一樣的字元——密碼要靠人抄寫與口述傳遞，
    # 同形字造成的「密碼明明對卻登不進去」會消耗掉大量支援時間。
    $chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
    -join (1..14 | ForEach-Object { $chars | Get-Random })
}

Say "以管理者登入 $BaseUrl"
$sec = Read-Host "管理者密碼（$AdminUser，不會顯示）" -AsSecureString
$adminPw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
             [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
try {
    $login = Invoke-RestMethod "$BaseUrl/api/auth/login" -Method POST -ContentType "application/json" `
        -Body (@{ username = $AdminUser; password = $adminPw } | ConvertTo-Json -Compress) -TimeoutSec 90
} catch {
    throw "登入失敗：$_`n（若後端剛部署，第一次請求要等冷啟動，稍後重試）"
}
if ($login.role -ne "admin") { throw "此帳號角色為 $($login.role)，需要 admin 才能開通帳號" }
$H = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "  ✓ 已登入（$($login.identity)）"

if ($List) {
    Say "現有帳號"
    $r = Invoke-RestMethod "$BaseUrl/api/v1/users" -Headers $H
    $r.users | Format-Table identity, role_zh, display_name, disabled, created_at -AutoSize
    exit 0
}

# 名單來源
if ($Csv) {
    if (-not (Test-Path $Csv)) { throw "找不到 $Csv" }
    $list = Import-Csv $Csv | ForEach-Object { @{ user = $_.user; role = $_.role; name = $_.name } }
} else {
    $list = $DEFAULT_USERS
}

Say "開通 $($list.Count) 組帳號"
$created = @()
foreach ($u in $list) {
    $pw = New-Password
    $body = @{ user = $u.user; role = $u.role; password = $pw; display_name = $u.name } | ConvertTo-Json -Compress
    try {
        $r = Invoke-RestMethod "$BaseUrl/api/v1/users" -Method POST -ContentType "application/json" `
                -Headers $H -Body $body -TimeoutSec 60
        $created += [pscustomobject]@{
            登入帳號 = "$($r.user.org):$($r.user.user)"
            角色     = $u.role
            姓名     = $u.name
            密碼     = $pw
        }
        Write-Host ("  ✓ {0,-10} {1,-10} {2}" -f $u.user, $u.role, $u.name)
    } catch {
        Warn ("  ✗ {0}：{1}" -f $u.user, $_.ErrorDetails.Message)
    }
}

# 密碼只在這裡出現一次——後端只存 PBKDF2 雜湊，之後任何人都取不回明文。
$out = Join-Path $PSScriptRoot ("accounts_{0}.csv" -f (Get-Date -Format "yyyyMMdd_HHmm"))
$created | Export-Csv -Path $out -NoTypeInformation -Encoding UTF8

Write-Host "`n═══════════════════════════════════════════════" -ForegroundColor Green
$created | Format-Table -AutoSize
Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
Write-Host "帳密已存：$out" -ForegroundColor Green

Warn @"

這個 CSV 含明文密碼：
  · 個別傳給本人後**立即刪除**，不要留在專案目錄、不要進版控（.gitignore 已排除）
  · 不要用同一封訊息傳給所有人——一次外洩就是全部
  · 後端只存雜湊，密碼遺失只能由管理者重新設定（無法取回）

給測試者的說明：
  登入帳號 = 上表「登入帳號」欄（含 default: 前綴，也可只填冒號後面那段）
  App 主畫面 →「設定」→ 後端位址填 $BaseUrl → 帳號密碼 → 連線測試

角色能做什麼（詳見 docs/rbac_design.md）：
  醫師     量測、存病歷、**修邊確認**、送訓練標註
  護理師   量測、存病歷、輸入滲液（修邊可做，但不產生「醫師已驗證」）
  助理     量測與拍照，不可存入病歷
  工程師   範例／模擬圖驗證、設定、佇列健康度；**看不到臨床資料**
"@
