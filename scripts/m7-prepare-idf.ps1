[CmdletBinding()]
param(
    [string]$SourcePath = "C:\esp\v6.0.1\esp-idf",
    [string]$WorktreePath = ""
)

$ErrorActionPreference = "Stop"

$expectedCommit = "8c19b156084a0753687347cca1f5355782893533"
$expectedSourceHashes = [ordered]@{
    "components/esp_hw_support/port/esp32p4/cpu_region_protect.c" =
        "26c4c6a1fed3aa64ef7b331fab905f54bb33674fe71db561412a157dc9db131f"
    "components/esp_lcd/dsi/esp_lcd_panel_dpi.c" =
        "f5f5ce836267020d72f5f9a7591647cc6606d8729fc26a12197889d03b3eeeb1"
    "components/esp_lcd/dsi/include/esp_lcd_mipi_dsi.h" =
        "eb60e0441b65229424eb55f9ffae4638186d4785a0279296c1b53c452dc44ba6"
}
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$patchPaths = @(
    Join-Path $repoPath "loader\patches\esp-idf-v6.0.1\0001-esp32p4-deny-u-mode-platform-regions.patch"
    Join-Path $repoPath "loader\patches\esp-idf-v6.0.1\0002-lcd-add-dpi-circular-handoff.patch"
)
if ([string]::IsNullOrWhiteSpace($WorktreePath)) {
    $WorktreePath = Join-Path $repoPath "build\idf-v6.0.1-m7"
}
$WorktreePath = [System.IO.Path]::GetFullPath($WorktreePath)
$SourcePath = [System.IO.Path]::GetFullPath($SourcePath)

function Invoke-GitChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & git @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

if (-not (Test-Path -LiteralPath (Join-Path $SourcePath ".git"))) {
    throw "ESP-IDF source is not a Git checkout: $SourcePath"
}
foreach ($patchPath in $patchPaths) {
    if (-not (Test-Path -LiteralPath $patchPath)) {
        throw "Missing tracked M7 ESP-IDF patch: $patchPath"
    }
}

$sourceHead = (Invoke-GitChecked @("-C", $SourcePath, "rev-parse", "HEAD") | Out-String).Trim()
$tagCommit = (Invoke-GitChecked @("-C", $SourcePath, "rev-parse", "v6.0.1^{commit}") | Out-String).Trim()
if ($sourceHead -ne $expectedCommit -or $tagCommit -ne $expectedCommit) {
    throw "Expected ESP-IDF v6.0.1 at $expectedCommit; source HEAD=$sourceHead tag=$tagCommit"
}
$sourceTargetChanges = Invoke-GitChecked @(
    "-C", $SourcePath, "status", "--porcelain", "--untracked-files=no", "--"
) | Where-Object {
    $path = $_.Substring(3).Replace("\", "/")
    $expectedSourceHashes.Contains($path)
}
if (($sourceTargetChanges | Out-String).Trim().Length -ne 0) {
    throw "Installed ESP-IDF target source is modified; refusing a mismatched baseline."
}

if (-not (Test-Path -LiteralPath $WorktreePath)) {
    $parent = Split-Path -Parent $WorktreePath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Invoke-GitChecked @(
        "-C", $SourcePath, "worktree", "add", "--detach", $WorktreePath, $expectedCommit
    ) | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $WorktreePath ".git"))) {
    throw "M7 ESP-IDF path is not the expected task-local Git worktree: $WorktreePath"
}

$worktreeHead = (Invoke-GitChecked @("-C", $WorktreePath, "rev-parse", "HEAD") | Out-String).Trim()
if ($worktreeHead -ne $expectedCommit) {
    throw "M7 ESP-IDF worktree is at $worktreeHead, expected $expectedCommit"
}
$changedPaths = @(
    Invoke-GitChecked @(
        "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
        "-C", $WorktreePath, "diff", "--name-only"
    ) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
foreach ($changedPath in $changedPaths) {
    if (-not $expectedSourceHashes.Contains($changedPath.Trim())) {
        throw "M7 ESP-IDF worktree contains unexpected change: $changedPath"
    }
}

$previousErrorActionPreference = $ErrorActionPreference
foreach ($patchPath in $patchPaths) {
    $ErrorActionPreference = "Continue"
    & git -C $WorktreePath apply --check $patchPath 2>$null
    $applyCheckExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($applyCheckExitCode -eq 0) {
        Invoke-GitChecked @("-C", $WorktreePath, "apply", $patchPath) | Out-Null
    } else {
        $ErrorActionPreference = "Continue"
        & git -C $WorktreePath apply --reverse --check $patchPath 2>$null
        $reverseCheckExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($reverseCheckExitCode -ne 0) {
            throw "M7 ESP-IDF worktree is neither pristine nor exactly patched for $patchPath."
        }
    }
}

foreach ($entry in $expectedSourceHashes.GetEnumerator()) {
    $patchedPath = Join-Path $WorktreePath ($entry.Key -replace "/", "\")
    $actualHash = (Get-FileHash -LiteralPath $patchedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $entry.Value) {
        throw "Patched ESP-IDF source digest mismatch for $($entry.Key): expected $($entry.Value), got $actualHash"
    }
}
foreach ($patchPath in $patchPaths) {
    $ErrorActionPreference = "Continue"
    & git -C $WorktreePath apply --reverse --check $patchPath 2>$null
    $reverseCheckExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($reverseCheckExitCode -ne 0) {
        throw "Tracked patch does not exactly describe the prepared ESP-IDF source: $patchPath"
    }
}

Invoke-GitChecked @(
    "-C", $WorktreePath, "submodule", "update", "--init", "--recursive", "--jobs", "8"
) | Out-Null

Write-Host "M7 ESP-IDF prepared: tag=v6.0.1 commit=$expectedCommit patches=$($patchPaths.Count) path=$WorktreePath"
