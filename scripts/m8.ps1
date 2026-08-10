[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [string]$IgniteVmSourceDir = $env:MICRONUX_IGNITEVM_SOURCE_DIR,
    [ValidateSet("present", "absent")]
    [string]$ExpectedMipiAdapter = "present",
    [switch]$SkipLinuxBuild,
    [switch]$SkipFlash,
    [switch]$SkipDeviceTest,
    [switch]$SkipCombinedTest
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslRepoPath = (& wsl.exe -- wslpath -a -u $repoPathArgument | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

if (-not $SkipLinuxBuild) {
    if ([string]::IsNullOrWhiteSpace($IgniteVmSourceDir)) {
        throw "Set -IgniteVmSourceDir or MICRONUX_IGNITEVM_SOURCE_DIR."
    }
    $igniteVmPath = (Resolve-Path -LiteralPath $IgniteVmSourceDir).Path
    $igniteVmPathArgument = $igniteVmPath.Replace("\", "/")
    $wslIgniteVmPath = (& wsl.exe -- wslpath -a -u $igniteVmPathArgument | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslIgniteVmPath)) {
        throw "Could not map the IgniteVM repository into WSL."
    }
    & wsl.exe -- env "MICRONUX_IGNITEVM_SOURCE_DIR=$wslIgniteVmPath" `
        MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m6-combined-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M8 Linux and IgniteVM build failed."
    }
}

$artifactPath = Join-Path $repoPath "out\m8"
$imagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
$ignitePath = Join-Path $artifactPath "micronux-ignite"
foreach ($artifact in @($imagePath, $dtbPath, $metadataPath, $ignitePath)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Missing M8 artifact: $artifact"
    }
}
if ((Get-Item -LiteralPath $imagePath).Length -gt 0x600000) {
    throw "M8 Image exceeds the 6 MiB Linux partition."
}
if ((Get-Item -LiteralPath $dtbPath).Length -gt 0x200000) {
    throw "M8 DTB exceeds the 2 MiB DTB partition."
}

$idfPython = "C:\Espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf)) {
    throw "Missing pinned ESP-IDF v6.0.1 Python: $idfPython"
}

if (-not $SkipFlash) {
    & $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
        --before default-reset --after hard-reset write-flash `
        0x200000 $imagePath `
        0x800000 $dtbPath `
        0xA00000 $metadataPath
    if ($LASTEXITCODE -ne 0) {
        throw "M8 Linux payload flash failed."
    }
}

if (-not $SkipDeviceTest) {
    & $idfPython (Join-Path $PSScriptRoot "m8-device-test.py") `
        --port $Port --artifact-dir $artifactPath `
        --log (Join-Path $artifactPath "m8-device-test.log")
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M8 device and IgniteVM gate failed."
    }
}

if (-not $SkipCombinedTest) {
    & $idfPython (Join-Path $PSScriptRoot "m6-combined-test.py") `
        --port $Port --boots 1 --artifact-dir $artifactPath `
        --expect-mipi-adapter $ExpectedMipiAdapter
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX combined microSD/C6 regression failed."
    }
}

Write-Host "M8 gate complete: loader=preserved payload=flashed device-api=1.0 ignitevm=passed"
