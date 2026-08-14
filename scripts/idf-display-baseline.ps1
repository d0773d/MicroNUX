[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [switch]$Flash,
    [switch]$ConfirmExactKitC
)

$ErrorActionPreference = "Stop"

if ($Flash -and -not $ConfirmExactKitC) {
    throw "Flashing requires -ConfirmExactKitC for the 800x1280 JD9365 panel."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$idfPath = "C:\esp\v6.0.1\esp-idf"
$buildPath = Join-Path $repoPath "build\idf-display-baseline"
$sdkconfigPath = Join-Path $buildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.display-standalone.defaults")
) -join ";"

$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& (Join-Path $idfPath "export.ps1") *> $null
$exportExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorPreference
if ($exportExitCode -ne 0) {
    throw "ESP-IDF v6.0.1 activation failed."
}

$idfVersion = (& idf.py --version | Out-String).Trim()
if ($idfVersion -ne "ESP-IDF v6.0.1") {
    throw "Expected ESP-IDF v6.0.1, got '$idfVersion'."
}

$env:CMAKE_GENERATOR = "Ninja"
$env:IDF_CCACHE_ENABLE = "1"
& idf.py -C $loaderPath -B $buildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "Standalone display configuration failed."
}

$configuration = Get-Content -LiteralPath $sdkconfigPath -Raw
$required = @(
    "CONFIG_MICRONUX_MIPI_DSI=y",
    "CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280=y",
    "CONFIG_MICRONUX_DISPLAY_STANDALONE=y"
)
foreach ($line in $required) {
    if ($configuration -notmatch "(?m)^$([regex]::Escape($line))\r?$") {
        throw "Missing standalone display setting: $line"
    }
}
$forbidden = @(
    "CONFIG_MICRONUX_M9_JD9365_COLD_RELINQUISH=y",
    "CONFIG_MICRONUX_C6_SDIO_PROFILE=y",
    "CONFIG_MICRONUX_SDMMC_DUAL_SLOT_PROFILE=y",
    "CONFIG_MICRONUX_C6_PROVISIONING=y"
)
foreach ($line in $forbidden) {
    if ($configuration -match "(?m)^$([regex]::Escape($line))\r?$") {
        throw "Standalone display unexpectedly enables: $line"
    }
}

& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "Standalone ESP-IDF display build failed."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
$elfPath = Join-Path $buildPath "micronux_m3_loader.elf"
$symbols = (& riscv32-esp-elf-nm $elfPath | Out-String)
$strings = (& riscv32-esp-elf-strings $elfPath | Out-String)
if ($strings -notmatch "MICRONUX:IDF-DISPLAY state=running" -or
    $strings -notmatch "linux=disabled sdmmc=disabled c6=disabled") {
    throw "Standalone display identity is missing from the linked image."
}
if ($symbols -match "micronux_handoff_jump|prepare_sdmmc_for_linux|micronux_dma_pms_prepare" -or
    $strings -match "MICRONUX:M3:JUMP|MICRONUX:M6:SDMMC") {
    throw "Standalone display image unexpectedly links Linux or SD/MMC handoff code."
}
$image = Get-Item -LiteralPath $imagePath
$digest = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "ESP-IDF display baseline built: image=$($image.Length)B sha256=$digest owner=esp-idf linux=disabled sdmmc=disabled c6=disabled"

if ($Flash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "Standalone ESP-IDF display flash failed."
    }
    Write-Host "ESP-IDF display baseline flashed: port=$Port panel=jd9365-800x1280"
} else {
    Write-Host "Build only. Nothing was flashed."
}
