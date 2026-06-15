# Windows 平台雲端端點連接測試腳本 (簡化版)
# 用於測試與雲端 AI 模型訓練及分析服務的連接

param(
    [string]$BaseUrl = "https://innate-plexus-461807-t3.de.r.appspot.com"
)

# 設定錯誤處理
$ErrorActionPreference = "Continue"

Write-Host "Windows 平台雲端端點連接測試工具 (PowerShell)" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Gray
Write-Host "目標服務: $BaseUrl" -ForegroundColor Cyan
Write-Host "測試開始時間: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Gray

# 測試結果收集
$TestResults = @()

function Write-TestResult {
    param(
        [string]$TestName,
        [bool]$Success,
        [string]$Message,
        [double]$ResponseTime = 0
    )
    
    $Status = if ($Success) { "OK" } else { "FAIL" }
    $Color = if ($Success) { "Green" } else { "Red" }
    Write-Host "$Status $TestName`: $Message" -ForegroundColor $Color
    
    if ($ResponseTime -gt 0) {
        Write-Host "   回應時間: $($ResponseTime.ToString('F3'))秒" -ForegroundColor Yellow
    }
    
    $TestResults += [PSCustomObject]@{
        TestName = $TestName
        Success = $Success
        Message = $Message
        ResponseTime = $ResponseTime
        Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    }
}

# 測試基本連接性
Write-Host "`n測試基本連接性..." -ForegroundColor Cyan
try {
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $Response = Invoke-WebRequest -Uri $BaseUrl -Method GET -TimeoutSec 10 -UseBasicParsing
    $Stopwatch.Stop()
    
    if ($Response.StatusCode -eq 200) {
        Write-TestResult -TestName "基本連接測試" -Success $true -Message "連接成功 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    } else {
        Write-TestResult -TestName "基本連接測試" -Success $false -Message "連接失敗 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    }
}
catch {
    Write-TestResult -TestName "基本連接測試" -Success $false -Message "連接錯誤: $($_.Exception.Message)"
}

# 測試健康檢查端點
Write-Host "`n測試健康檢查端點..." -ForegroundColor Cyan
try {
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $Response = Invoke-WebRequest -Uri "$BaseUrl/health" -Method GET -TimeoutSec 10 -UseBasicParsing
    $Stopwatch.Stop()
    
    if ($Response.StatusCode -eq 200) {
        try {
            $HealthData = $Response.Content | ConvertFrom-Json
            Write-TestResult -TestName "健康檢查端點" -Success $true -Message "服務健康: $($HealthData | ConvertTo-Json -Compress)" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
        }
        catch {
            Write-TestResult -TestName "健康檢查端點" -Success $false -Message "回應格式錯誤 (非 JSON)" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
        }
    } else {
        Write-TestResult -TestName "健康檢查端點" -Success $false -Message "健康檢查失敗 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    }
}
catch {
    Write-TestResult -TestName "健康檢查端點" -Success $false -Message "請求錯誤: $($_.Exception.Message)"
}

# 測試醫師認證端點
Write-Host "`n測試醫師認證端點..." -ForegroundColor Cyan
try {
    $AuthData = @{
        doctor_id = "test_doctor_001"
        password = "REPLACE_ME_TEST_PASSWORD"
        hospital = "測試醫院"
    } | ConvertTo-Json
    
    $Headers = @{
        "Content-Type" = "application/json"
        "User-Agent" = "WoundMeasurement-Windows-Client/1.0"
    }
    
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $Response = Invoke-WebRequest -Uri "$BaseUrl/auth/login" -Method POST -Body $AuthData -Headers $Headers -TimeoutSec 10 -UseBasicParsing
    $Stopwatch.Stop()
    
    if ($Response.StatusCode -in @(200, 401, 422)) {
        Write-TestResult -TestName "醫師認證端點" -Success $true -Message "端點可達 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    } else {
        Write-TestResult -TestName "醫師認證端點" -Success $false -Message "端點錯誤 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    }
}
catch {
    Write-TestResult -TestName "醫師認證端點" -Success $false -Message "請求錯誤: $($_.Exception.Message)"
}

# 測試標註資料上傳端點
Write-Host "`n測試標註資料上傳端點..." -ForegroundColor Cyan
try {
    $AnnotationData = @{
        doctor_id = "test_doctor_001"
        patient_id = "test_patient_001"
        image_filename = "test_wound.jpg"
        bjwat_scores = @{
            size = 3
            depth = 2
            edges = 2
            undermining = 1
            necrotic_tissue = 1
            exudate = 2
            granulation = 2
            epithelialization = 1
        }
        revpwat_scores = @{
            surface_area = 3
            depth = 2
            edges = 2
            undermining = 1
            necrotic_tissue = 1
            exudate = 2
        }
    } | ConvertTo-Json -Depth 3
    
    $Headers = @{
        "Content-Type" = "application/json"
        "User-Agent" = "WoundMeasurement-Windows-Client/1.0"
    }
    
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $Response = Invoke-WebRequest -Uri "$BaseUrl/upload/annotation" -Method POST -Body $AnnotationData -Headers $Headers -TimeoutSec 15 -UseBasicParsing
    $Stopwatch.Stop()
    
    if ($Response.StatusCode -in @(200, 201, 422)) {
        Write-TestResult -TestName "標註上傳端點" -Success $true -Message "端點可達 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    } else {
        Write-TestResult -TestName "標註上傳端點" -Success $false -Message "端點錯誤 (狀態碼: $($Response.StatusCode))" -ResponseTime $Stopwatch.Elapsed.TotalSeconds
    }
}
catch {
    Write-TestResult -TestName "標註上傳端點" -Success $false -Message "請求錯誤: $($_.Exception.Message)"
}

# 測試網路效能
Write-Host "`n測試網路效能..." -ForegroundColor Cyan
try {
    $Times = @()
    
    for ($i = 1; $i -le 3; $i++) {
        $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $Response = Invoke-WebRequest -Uri "$BaseUrl/health" -Method GET -TimeoutSec 10 -UseBasicParsing
        $Stopwatch.Stop()
        
        if ($Response.StatusCode -eq 200) {
            $Times += $Stopwatch.Elapsed.TotalSeconds
        } else {
            Write-TestResult -TestName "網路效能測試" -Success $false -Message "第 $i 次請求失敗"
            break
        }
        
        Start-Sleep -Milliseconds 200
    }
    
    if ($Times.Count -eq 3) {
        $AvgTime = ($Times | Measure-Object -Average).Average
        $MinTime = ($Times | Measure-Object -Minimum).Minimum
        $MaxTime = ($Times | Measure-Object -Maximum).Maximum
        
        Write-TestResult -TestName "網路效能測試" -Success $true -Message "平均回應時間: $($AvgTime.ToString('F3'))秒 (最小: $($MinTime.ToString('F3'))秒, 最大: $($MaxTime.ToString('F3'))秒)"
    }
}
catch {
    Write-TestResult -TestName "網路效能測試" -Success $false -Message "效能測試失敗: $($_.Exception.Message)"
}

# 生成報告
Write-Host "`n生成測試報告..." -ForegroundColor Cyan

$TotalTests = $TestResults.Count
$SuccessfulTests = ($TestResults | Where-Object { $_.Success }).Count
$SuccessRate = if ($TotalTests -gt 0) { ($SuccessfulTests / $TotalTests * 100) } else { 0 }

$ResponseTimes = $TestResults | Where-Object { $_.ResponseTime -gt 0 } | ForEach-Object { $_.ResponseTime }
$AvgResponseTime = if ($ResponseTimes.Count -gt 0) { ($ResponseTimes | Measure-Object -Average).Average } else { 0 }

$Report = @{
    TestSummary = @{
        TotalTests = $TotalTests
        SuccessfulTests = $SuccessfulTests
        SuccessRate = $SuccessRate
        AverageResponseTime = $AvgResponseTime
        TestTimestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        BaseUrl = $BaseUrl
    }
    TestResults = $TestResults
}

$ReportPath = "cloud_connection_test_report.json"
$Report | ConvertTo-Json -Depth 10 | Out-File -FilePath $ReportPath -Encoding UTF8

Write-Host "============================================================" -ForegroundColor Gray
Write-Host "測試完成" -ForegroundColor Green
Write-Host "測試結束時間: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "測試報告已保存至: $ReportPath" -ForegroundColor Green
Write-Host "成功率: $($SuccessRate.ToString('F1'))% ($SuccessfulTests/$TotalTests)" -ForegroundColor Yellow
Write-Host "平均回應時間: $($AvgResponseTime.ToString('F3'))秒" -ForegroundColor Yellow 