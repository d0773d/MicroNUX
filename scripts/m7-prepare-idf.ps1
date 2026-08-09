[CmdletBinding()]
param(
    [string]$SourcePath = "C:\esp\v6.0.1\esp-idf",
    [string]$WorktreePath = ""
)

$ErrorActionPreference = "Stop"

$expectedCommit = "8c19b156084a0753687347cca1f5355782893533"
$expectedSourceHash = "26c4c6a1fed3aa64ef7b331fab905f54bb33674fe71db561412a157dc9db131f"
$patchedRelativePath = "components/esp_hw_support/port/esp32p4/cpu_region_protect.c"
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$patchPath = Join-Path $repoPath "loader\patches\esp-idf-v6.0.1\0001-esp32p4-deny-u-mode-platform-regions.patch"
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
if (-not (Test-Path -LiteralPath $patchPath)) {
    throw "Missing tracked M7 ESP-IDF patch: $patchPath"
}

$sourceHead = (Invoke-GitChecked @("-C", $SourcePath, "rev-parse", "HEAD") | Out-String).Trim()
$tagCommit = (Invoke-GitChecked @("-C", $SourcePath, "rev-parse", "v6.0.1^{commit}") | Out-String).Trim()
if ($sourceHead -ne $expectedCommit -or $tagCommit -ne $expectedCommit) {
    throw "Expected ESP-IDF v6.0.1 at $expectedCommit; source HEAD=$sourceHead tag=$tagCommit"
}
$sourceTargetChanges = Invoke-GitChecked @(
    "-C", $SourcePath, "status", "--porcelain", "--untracked-files=no", "--", $patchedRelativePath
)
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
if ($changedPaths.Count -gt 0 -and
    ($changedPaths.Count -ne 1 -or $changedPaths[0].Trim() -ne $patchedRelativePath)) {
    throw "M7 ESP-IDF worktree contains unexpected changes: $($changedPaths -join ', ')"
}

$previousErrorActionPreference = $ErrorActionPreference
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
        throw "M7 ESP-IDF worktree is neither pristine nor exactly patched."
    }
}

$patchedPath = Join-Path $WorktreePath ($patchedRelativePath -replace "/", "\")
$actualHash = (Get-FileHash -LiteralPath $patchedPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedSourceHash) {
    throw "Patched ESP-IDF source digest mismatch: expected $expectedSourceHash, got $actualHash"
}
$ErrorActionPreference = "Continue"
& git -C $WorktreePath apply --reverse --check $patchPath 2>$null
$reverseCheckExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
if ($reverseCheckExitCode -ne 0) {
    throw "Tracked patch does not exactly describe the prepared ESP-IDF source."
}

Invoke-GitChecked @(
    "-C", $WorktreePath, "submodule", "update", "--init", "--recursive", "--jobs", "8"
) | Out-Null

Write-Host "M7 ESP-IDF prepared: tag=v6.0.1 commit=$expectedCommit source_sha256=$actualHash path=$WorktreePath"
