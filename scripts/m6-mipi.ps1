[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("jd9365", "ili9881c", "hx8394", "ek79007")]
    [string]$Panel,
    [string]$Port = "COM14",
    [switch]$Flash,
    [switch]$ConfirmExactPanel
)

$ErrorActionPreference = "Stop"

if ($Flash -and -not $ConfirmExactPanel) {
    throw "Refusing to power MIPI-DSI without -ConfirmExactPanel. Verify the panel controller printed on the display or adapter first."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$profile = $Panel.ToLowerInvariant()
$profileDefaults = Join-Path $loaderPath "sdkconfig.mipi-$profile.defaults"
if (-not (Test-Path -LiteralPath $profileDefaults)) {
    throw "Missing MIPI-DSI defaults: $profileDefaults"
}

$idfPath = "C:\esp\v6.0.1\esp-idf"
$buildPath = Join-Path $repoPath "build\loader-mipi-$profile"
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
$sdkconfigPath = Join-Path $buildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.combined.defaults"),
    $profileDefaults
) -join ";"

& idf.py -C $loaderPath -B $buildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M6 MIPI-DSI loader configure failed for '$profile'."
}

$configuration = Get-Content -LiteralPath $sdkconfigPath -Raw
$panelSymbols = @{
    jd9365 = "CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280"
    ili9881c = "CONFIG_MICRONUX_MIPI_PANEL_ILI9881C_720_1280"
    hx8394 = "CONFIG_MICRONUX_MIPI_PANEL_HX8394_720_1280"
    ek79007 = "CONFIG_MICRONUX_MIPI_PANEL_EK79007_1024_600"
}
if ($configuration -notmatch "(?m)^CONFIG_MICRONUX_MIPI_DSI=y\r?$") {
    throw "Generated configuration did not enable the exact MIPI profile."
}
if ($configuration -match "(?m)^CONFIG_MICRONUX_MIPI_DSI_PROBE=y\r?$") {
    throw "Unsafe configuration: the attachment probe and exact MIPI profile are both enabled."
}
$selectedSymbol = $panelSymbols[$profile]
if ($configuration -notmatch "(?m)^$selectedSymbol=y\r?$") {
    throw "Generated configuration did not select '$profile'."
}
if ($configuration -notmatch "(?m)^CONFIG_MICRONUX_C6_SDIO_PROFILE=y\r?$" -or
    $configuration -notmatch "(?m)^CONFIG_MICRONUX_SDMMC_DUAL_SLOT_PROFILE=y\r?$") {
    throw "Generated configuration did not preserve the combined microSD/C6 baseline."
}
& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M6 MIPI-DSI loader build failed for '$profile'."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
$elfPath = Join-Path $buildPath "micronux_m3_loader.elf"
$symbols = (& riscv32-esp-elf-nm $elfPath | Out-String)
if ($symbols -notmatch "micronux_panel_guard_begin" -or
    $symbols -notmatch "micronux_panel_guard_end") {
    throw "MIPI safety guard is missing from the linked image."
}
if ($profile -ne "ek79007" -and
    $symbols -notmatch "__wrap_i2c_bus_write_bytes") {
    throw "Vendor backlight writes are not redirected through the safety guard."
}
$image = Get-Item -LiteralPath $imagePath
$digest = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "M6 MIPI-DSI build: panel=$profile image=$($image.Length)B sha256=$digest vendor-backlight=guarded baseline=combined"

if ($Flash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M6 MIPI-DSI loader flash failed for '$profile'."
    }
    Write-Host "M6 MIPI-DSI flashed: panel=$profile port=$Port confirmation=exact-panel"
} else {
    Write-Host "Build only. Nothing was flashed and no display rail was powered."
}
