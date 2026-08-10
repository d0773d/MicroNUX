[CmdletBinding()]
param(
    [string]$Port = "COM13",
    [switch]$SkipLinuxBuild,
    [switch]$Flash,
    [switch]$ConfirmExactKitC
)

$ErrorActionPreference = "Stop"

if ($Flash -and -not $ConfirmExactKitC) {
    throw "Flashing M9 requires -ConfirmExactKitC."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslRepoPath = (& wsl.exe -- wslpath -a -u $repoPathArgument | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

if (-not $SkipLinuxBuild) {
    & wsl.exe -- env MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m9-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M9 Linux input and presentation build failed."
    }
}

$artifactPath = Join-Path $repoPath "out\m9"
$linuxImagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
$displayTestPath = Join-Path $artifactPath "micronux-display-test"
foreach ($artifact in @(
    $linuxImagePath,
    $dtbPath,
    $metadataPath,
    $displayTestPath
)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Missing M9 artifact: $artifact"
    }
}
if ((Get-Item -LiteralPath $linuxImagePath).Length -gt 0x600000) {
    throw "M9 Image exceeds the 6 MiB Linux partition."
}
if ((Get-Item -LiteralPath $dtbPath).Length -gt 0x200000) {
    throw "M9 DTB exceeds the 2 MiB DTB partition."
}

if (-not $Flash) {
    Write-Host "M9 build and artifact validation passed. Nothing was flashed."
    exit 0
}

$idfPath = Join-Path $repoPath "build\idf-v6.0.1-m7"
& (Join-Path $PSScriptRoot "m7-prepare-idf.ps1") -WorktreePath $idfPath

$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& (Join-Path $idfPath "export.ps1") *> $null
$exportExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorPreference
if ($exportExitCode -ne 0) {
    throw "Task-local ESP-IDF v6.0.1 activation failed."
}
if ([System.IO.Path]::GetFullPath($env:IDF_PATH) -ne
    [System.IO.Path]::GetFullPath($idfPath)) {
    throw "ESP-IDF activation selected '$env:IDF_PATH' instead of '$idfPath'."
}
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf)) {
    throw "Task-local ESP-IDF Python entrypoint is unavailable."
}
$idfVersion = (& $idfPython (Join-Path $idfPath "tools\idf.py") --version |
    Out-String).Trim()
if ($idfVersion -ne "ESP-IDF v6.0.1-dirty") {
    throw "Expected the verified patched ESP-IDF v6.0.1 worktree, got '$idfVersion'."
}

& $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
    --before default-reset --after hard-reset write-flash `
    0x200000 $linuxImagePath `
    0x800000 $dtbPath `
    0xA00000 $metadataPath
if ($LASTEXITCODE -ne 0) {
    throw "M9 Linux payload flash failed."
}

Write-Host "M9 Linux payload flashed: port=$Port touch=GT9271-poll display=800x1280 diagnostic=installed"
