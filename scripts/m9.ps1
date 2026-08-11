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
$loaderPath = Join-Path $repoPath "loader"
$idfPath = Join-Path $repoPath "build\idf-v6.0.1-m7"
$loaderBuildPath = Join-Path $repoPath "build\loader-m9-kit-c"
$linuxImagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
$displayTestPath = Join-Path $artifactPath "micronux-display-test"
$rootfsPath = Join-Path $artifactPath "rootfs.cpio"
$linuxConfigPath = Join-Path $artifactPath "linux.config"
$vmlinuxPath = Join-Path $artifactPath "vmlinux"
$sha256SumsPath = Join-Path $artifactPath "SHA256SUMS"
$sourceContractPath = Join-Path $artifactPath "SOURCE-CONTRACT"
foreach ($artifact in @(
    $linuxImagePath,
    $dtbPath,
    $metadataPath,
    $displayTestPath,
    $rootfsPath,
    $linuxConfigPath,
    $vmlinuxPath,
    $sha256SumsPath,
    $sourceContractPath
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

$contractOutput = (& wsl.exe -- bash "$wslRepoPath/scripts/m9-build.sh" --print-contract |
    Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Could not calculate the current M9 Linux source contract."
}
$contractMatch = [regex]::Match(
    $contractOutput,
    '(?m)^MICRONUX:M9:SOURCE-CONTRACT sha256=([0-9a-f]{64})\r?$'
)
if (-not $contractMatch.Success) {
    throw "The M9 Linux source contract output was malformed."
}
$currentSourceContract = $contractMatch.Groups[1].Value
$artifactSourceContract = (Get-Content -LiteralPath $sourceContractPath -Raw).Trim()
if ($artifactSourceContract -ne $currentSourceContract) {
    throw "M9 Linux artifacts are stale for the current source contract. Re-run without -SkipLinuxBuild."
}

$artifactFiles = [ordered]@{
    "Image" = $linuxImagePath
    "esp32p4-micronux.dtb" = $dtbPath
    "metadata.bin" = $metadataPath
    "rootfs.cpio" = $rootfsPath
    "linux.config" = $linuxConfigPath
    "vmlinux" = $vmlinuxPath
    "micronux-display-test" = $displayTestPath
}
$expectedDigests = @{}
foreach ($line in Get-Content -LiteralPath $sha256SumsPath) {
    if ($line -notmatch '^([0-9a-f]{64})  ([^/\\]+)$') {
        throw "Malformed M9 artifact checksum line: '$line'"
    }
    $name = $Matches[2]
    if ($expectedDigests.ContainsKey($name)) {
        throw "Duplicate M9 artifact checksum: $name"
    }
    $expectedDigests[$name] = $Matches[1]
}
if ($expectedDigests.Count -ne $artifactFiles.Count) {
    throw "M9 artifact checksum manifest does not contain exactly $($artifactFiles.Count) entries."
}
foreach ($entry in $artifactFiles.GetEnumerator()) {
    if (-not $expectedDigests.ContainsKey($entry.Key)) {
        throw "M9 artifact checksum manifest is missing '$($entry.Key)'."
    }
    $actualDigest = (Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualDigest -ne $expectedDigests[$entry.Key]) {
        throw "M9 artifact checksum mismatch: $($entry.Key)"
    }
}

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
$idfPy = Join-Path $idfPath "tools\idf.py"
if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf) -or
    -not (Test-Path -LiteralPath $idfPy -PathType Leaf)) {
    throw "Task-local ESP-IDF Python entrypoint is unavailable."
}
$idfVersion = (& $idfPython $idfPy --version |
    Out-String).Trim()
if ($idfVersion -ne "ESP-IDF v6.0.1-dirty") {
    throw "Expected the verified patched ESP-IDF v6.0.1 worktree, got '$idfVersion'."
}

$env:CMAKE_GENERATOR = "Ninja"
$env:IDF_CCACHE_ENABLE = "1"
$sdkconfigPath = Join-Path $loaderBuildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.combined.defaults"),
    (Join-Path $loaderPath "sdkconfig.mipi-jd9365.defaults"),
    (Join-Path $loaderPath "sdkconfig.m7.defaults")
) -join ";"

& $idfPython $idfPy -C $loaderPath -B $loaderBuildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader configure failed."
}
$configuration = Get-Content -LiteralPath $sdkconfigPath -Raw
foreach ($required in @(
    "CONFIG_MICRONUX_M7_EARLY_UMODE_DENY=y",
    "CONFIG_MICRONUX_C6_SDIO_PROFILE=y",
    "CONFIG_MICRONUX_SDMMC_DUAL_SLOT_PROFILE=y",
    "CONFIG_MICRONUX_MIPI_DSI=y",
    "CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280=y"
)) {
    if ($configuration -notmatch "(?m)^$([regex]::Escape($required))\r?$") {
        throw "M9 loader configuration is missing '$required'."
    }
}

& ninja -C $loaderBuildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader build failed."
}
$loaderImagePath = Join-Path $loaderBuildPath "micronux_m3_loader.bin"
$loaderElfPath = Join-Path $loaderBuildPath "micronux_m3_loader.elf"
foreach ($loaderArtifact in @($loaderImagePath, $loaderElfPath)) {
    if (-not (Test-Path -LiteralPath $loaderArtifact -PathType Leaf)) {
        throw "Missing M9 loader artifact: $loaderArtifact"
    }
}
$loaderStrings = (& riscv32-esp-elf-strings $loaderElfPath | Out-String)
foreach ($marker in @(
    "MICRONUX:M7:DSI-BLANK state=ready backlight=off settle_ms=%lu restore=linux-after-status-ready",
    "lane_clock=forced-hs video_lp=disabled",
    "rearm=linux-after-status-ready"
)) {
    if ($loaderStrings -notmatch [regex]::Escape($marker)) {
        throw "M9 loader is missing marker '$marker'."
    }
}
$loaderDigest = (Get-FileHash -LiteralPath $loaderImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "M9 loader built: image=$((Get-Item -LiteralPath $loaderImagePath).Length)B sha256=$loaderDigest idf=v6.0.1 kit-c=jd9365"

if (-not $Flash) {
    Write-Host "M9 Linux and loader build validation passed. Nothing was flashed."
    exit 0
}

& $idfPython (Join-Path $PSScriptRoot "usb-reset-arm.py") --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the MicroNUX USB reset path for flashing."
}

& $idfPython $idfPy -C $loaderPath -B $loaderBuildPath -p $Port flash
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader flash failed."
}

& $idfPython (Join-Path $PSScriptRoot "usb-reset-arm.py") --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Could not re-arm the MicroNUX USB reset path after loader flash."
}

& $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
    --before default-reset --after hard-reset write-flash `
    0x200000 $linuxImagePath `
    0x800000 $dtbPath `
    0xA00000 $metadataPath
if ($LASTEXITCODE -ne 0) {
    throw "M9 Linux payload flash failed."
}

Write-Host "M9 loader and Linux payload flashed: port=$Port touch=GT9271-poll display=800x1280 diagnostic=installed"
