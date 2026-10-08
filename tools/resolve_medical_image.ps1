# Read-only image provenance gate shared by medical deployment entry points.
function Get-VerifiedMedicalImage {
    param([string]$ProjectId, [string]$Region, [string]$GitCommit,
          [string]$BuildId, [string]$BuildManifestSha256)
    if ($BuildId -cnotmatch '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' -or
        $BuildManifestSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "an explicit successful medical BuildId and BuildManifestSha256 are required; implicit source builds are disabled"
    }
    $resolver = Join-Path $PSScriptRoot 'medical_image_build.py'
    $result = @(& python -B $resolver resolve --build-id $BuildId --git-commit $GitCommit `
        --manifest-sha256 $BuildManifestSha256 --project $ProjectId --region $Region)
    if ($LASTEXITCODE -ne 0 -or $result.Count -ne 1) {
        throw "medical build evidence could not be verified; do not deploy"
    }
    $image = [string]$result[0]
    if ($image -cnotmatch '^asia-east1-docker\.pkg\.dev/woundai-jackh001/woundai-medical-build/medical@sha256:[0-9a-f]{64}$') {
        throw "medical build resolver did not return the expected immutable image"
    }
    return $image
}
