# 開通臨床測試者帳號（RBAC S1）。
#
# 用法：
#   .\provision_users.ps1 -BaseUrl https://woundai-backend-421209514056.asia-east1.run.app
#   .\provision_users.ps1 -BaseUrl <網址> -Csv users.csv     # 自訂名單
#   .\provision_users.ps1 -BaseUrl <網址> -List              # 只列出現有帳號
#   .\provision_users.ps1 -BaseUrl <網址> -Disable dr.chen,ns.liu   # 停用指定帳號
#
# 預設開通 10 組臨床帳號（見 $DEFAULT_USERS）。密碼隨機產生並輸出成 CSV，
# **只會顯示這一次**——後端只存 PBKDF2 雜湊，取不回明文。
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存。

param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [string]$AdminUser = "admin",
    [string]$Csv,
    [switch]$List,
    # 停用帳號。**不提供刪除**——稽核軌跡引用了這些識別碼，刪掉會讓歷史紀錄
    # 指向一個不存在的人，那正是稽核最不能發生的事。停用即刻失效，紀錄留痕。
    [string[]]$Disable,
    # 只修復顯示名稱，**不變更密碼、不變更啟用狀態**。
    # 為 2026-08-04 那批被 ISO-8859-1 編碼吃掉的中文名（存成 "?? 01"）而加——
    # 重跑整個開通流程會重新產生密碼，等於為了修一個顯示欄位去作廢十組已配發的帳密。
    [switch]$FixNames
)

$ErrorActionPreference = "Continue"
$BaseUrl = $BaseUrl.TrimEnd('/')
function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }

# 預設名單：**編號式，不含任何人的姓名**。
#
# 為什麼不用姓氏（如 dr.chen）：
#
#  1. **識別碼會永久留在稽核軌跡裡**。稽核是 append-only，日後還要進 WORM 桶——
#     姓氏一旦寫進去就再也拿不掉。編號讓後端從頭到尾不知道任何人的姓名。
#  2. 這與整個架構的模式一致：`WD-code ↔ 病患` 的對照表留在手機、
#     `帳號 ↔ 真人` 的對照表留在院方手上。後端只認假名。
#  3. 配發實務上也比較好用——不會發生「陳醫師的帳號給了林醫師」這種尷尬，
#     人員異動時直接停用編號、配發新編號即可。
#
# 角色比例反映真實傷口照護團隊：護理師是量測主力，醫師負責 GT 確認（人數少但不可或缺）。
$DEFAULT_USERS = @(
    @{ user = "dr01";  role = "physician"; name = "醫師 01" }
    @{ user = "dr02";  role = "physician"; name = "醫師 02" }
    @{ user = "dr03";  role = "physician"; name = "醫師 03" }
    @{ user = "ns01";  role = "nurse";     name = "護理師 01" }
    @{ user = "ns02";  role = "nurse";     name = "護理師 02" }
    @{ user = "ns03";  role = "nurse";     name = "護理師 03" }
    @{ user = "ns04";  role = "nurse";     name = "護理師 04" }
    @{ user = "as01";  role = "assistant"; name = "助理 01" }
    @{ user = "as02";  role = "assistant"; name = "助理 02" }
    @{ user = "eng01"; role = "engineer";  name = "工程師 01" }
)

# ⚠ **PowerShell 5.1 的 Invoke-RestMethod 預設不以 UTF-8 送出請求主體。**
#
# `-ContentType "application/json"`（沒帶 charset）時，5.1 會把字串主體以
# **ISO-8859-1** 編碼送出。中文全部落在該編碼之外，於是每個中文字變成 `?`——
# 而請求仍然成功（HTTP 200），伺服器把 `?` 當成合法字串照樣存起來。
#
# 這是最惡劣的一類 bug：**沒有錯誤、沒有警告、資料已經壞了**。
# 症狀直到很久以後才會出現，而且是在別的地方——帳號列表顯示「?? 01」、
# App 登入橫幅顯示「??? 01」，看起來像顯示問題，實際上是資料庫裡真的存著問號。
#
# 判別方法（這次就是這樣抓到的）：`role_zh` 欄（伺服器端常數）中文正常，
# 而 `display_name` 欄（客戶端送上去的）是問號 —— 同一個回應、同一個主控台，
# 所以不可能是傳輸或終端機編碼，只可能是**送出時就已經壞了**。
#
# 解法：自己把主體編成 UTF-8 位元組陣列再送。位元組陣列不會被重新編碼。
function Invoke-JsonApi {
    param([string]$Uri, [string]$Method = "GET", $Body, [hashtable]$Headers,
          [int]$TimeoutSec = 60)
    $p = @{ Uri = $Uri; Method = $Method; TimeoutSec = $TimeoutSec }
    if ($Headers) { $p.Headers = $Headers }
    if ($null -ne $Body) {
        $json = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Compress }
        $p.Body = [Text.Encoding]::UTF8.GetBytes($json)
        $p.ContentType = "application/json; charset=utf-8"
    }
    Invoke-RestMethod @p
}

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
    $login = Invoke-JsonApi "$BaseUrl/api/auth/login" -Method POST `
        -Body @{ username = $AdminUser; password = $adminPw } -TimeoutSec 90
} catch {
    throw "登入失敗：$_`n（若後端剛部署，第一次請求要等冷啟動，稍後重試）"
}
if ($login.role -ne "admin") { throw "此帳號角色為 $($login.role)，需要 admin 才能開通帳號" }
$H = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "  ✓ 已登入（$($login.identity)）"

if ($List) {
    Say "現有帳號"
    $r = Invoke-JsonApi "$BaseUrl/api/v1/users" -Headers $H
    $r.users | Format-Table identity, role_zh, display_name, disabled, created_at -AutoSize
    exit 0
}

if ($Disable) {
    Say "停用 $($Disable.Count) 組帳號"
    foreach ($u in $Disable) {
        $name = $u.Trim()
        if ($name -like "*:*") { $name = $name.Split(":")[-1] }
        # 停用要保留原角色——upsert 是整筆覆寫，role 少傳就會被後端拒絕。
        $existing = (Invoke-JsonApi "$BaseUrl/api/v1/users" -Headers $H).users |
                    Where-Object { $_.user -eq $name }
        if (-not $existing) { Warn "  ✗ 查無 $name"; continue }
        try {
            Invoke-JsonApi "$BaseUrl/api/v1/users" -Method POST -Headers $H -Body @{
                user = $name; role = $existing.role; disabled = $true } | Out-Null
            Write-Host "  ✓ 已停用 $name（紀錄留痕，稽核軌跡不受影響）"
        } catch { Warn ("  ✗ {0}：{1}" -f $name, $_.ErrorDetails.Message) }
    }
    exit 0
}

# 名單來源
#
# ⚠ 變數名不可用 $list —— **PowerShell 變數名不分大小寫**，會撞到上面的 [switch]$List，
# 於是 `$list = $DEFAULT_USERS` 變成「把陣列指派給 switch 參數」→ 型別轉換失敗，
# 而錯誤訊息只說「無法將 System.Object[] 轉換為 SwitchParameter」，
# 完全看不出是變數命名衝突。（與 Gc 撞 Get-Content 是同一類問題。）
if ($Csv) {
    if (-not (Test-Path $Csv)) { throw "找不到 $Csv" }
    $roster = @(Import-Csv $Csv | ForEach-Object {
        @{ user = $_.user; role = $_.role; name = $_.name }
    })
} else {
    $roster = $DEFAULT_USERS
}

# 出發前檢查：名單壞掉時大聲失敗，而不是送出一堆空的請求讓後端逐一拒絕。
$bad = @($roster | Where-Object { -not $_.user -or -not $_.role })
if ($roster.Count -eq 0) { throw "名單是空的。" }
if ($bad.Count -gt 0) { throw "名單有 $($bad.Count) 筆缺 user 或 role，請檢查 CSV 欄位名稱（user/role/name）。" }

if ($FixNames) {
    Say "修復 $($roster.Count) 組帳號的顯示名稱（不動密碼、不動啟用狀態）"
    foreach ($u in $roster) {
        try {
            # 不傳 password → 後端沿用既有雜湊；不傳 disabled → 沿用目前狀態。
            $r = Invoke-JsonApi "$BaseUrl/api/v1/users" -Method POST -Headers $H -Body @{
                    user = $u.user; role = $u.role; display_name = $u.name }
            Write-Host ("  ✓ {0,-8} → {1}" -f $u.user, $r.user.display_name)
        } catch { Warn ("  ✗ {0}：{1}" -f $u.user, $_.ErrorDetails.Message) }
    }
    Say "確認結果"
    (Invoke-JsonApi "$BaseUrl/api/v1/users" -Headers $H).users |
        Where-Object { -not $_.disabled } |
        Format-Table identity, role_zh, display_name, created_at -AutoSize
    Write-Host "顯示名稱若仍是問號,代表這一版腳本沒生效——確認檔案含 Invoke-JsonApi。" -ForegroundColor Yellow
    exit 0
}

Say "開通 $($roster.Count) 組帳號"
$created = @()
foreach ($u in $roster) {
    $pw = New-Password
    try {
        $r = Invoke-JsonApi "$BaseUrl/api/v1/users" -Method POST -Headers $H -Body @{
                user = $u.user; role = $u.role; password = $pw; display_name = $u.name }
        $created += [pscustomobject]@{
            登入帳號 = "$($r.user.org):$($r.user.user)"
            角色     = $u.role
            顯示名稱 = $u.name
            密碼     = $pw
            # 空欄，由院方填入實際配發對象。**這一欄只存在於這份 CSV，不會上傳**——
            # 帳號↔真人的對照表由院方保管，與 WD-code↔病患 的對照留在手機是同一個模式。
            配發給   = ""
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

配發方式：
  在 CSV 的「配發給」欄填上實際使用者，**那份對照表留在院方**，不要回傳給系統。
  後端只認 dr01/ns01 這類編號，從頭到尾不知道任何人的姓名——
  稽核軌跡因此可歸屬（有編號）又不含個資（無姓名）。

給測試者的說明：
  登入帳號 = 上表「登入帳號」欄（含 default: 前綴，也可只填冒號後面那段）
  App 主畫面 →「設定」→ 後端位址填 $BaseUrl → 帳號密碼 → 連線測試

角色能做什麼（詳見 docs/rbac_design.md）：
  醫師     量測、存病歷、**修邊確認**、送訓練標註
  護理師   量測、存病歷、輸入滲液（修邊可做，但不產生「醫師已驗證」）
  助理     量測與拍照，不可存入病歷
  工程師   範例／模擬圖驗證、設定、佇列健康度；**看不到臨床資料**
"@
