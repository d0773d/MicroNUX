[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [switch]$Flash,
    [switch]$ConfirmAttachmentProbe
)

$ErrorActionPreference = "Stop"

if ($Flash -and -not $ConfirmAttachmentProbe) {
    throw "Refusing to flash without -ConfirmAttachmentProbe. The probe performs no target writes and leaves the D-PHY off, but the P4 will reset."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$idfPath = "C:\esp\v6.0.1\esp-idf"
$buildPath = Join-Path $repoPath "build\loader-mipi-probe"
$sdkconfigPath = Join-Path $buildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.combined.defaults"),
    (Join-Path $loaderPath "sdkconfig.mipi-probe.defaults")
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
    throw "M6 MIPI attachment-probe configure failed."
}

$configuration = Get-Content -LiteralPath $sdkconfigPath -Raw
if ($configuration -notmatch "(?m)^CONFIG_MICRONUX_MIPI_DSI_PROBE=y\r?$") {
    throw "Generated configuration did not enable the attachment probe."
}
if ($configuration -match "(?m)^CONFIG_MICRONUX_MIPI_DSI=y\r?$") {
    throw "Unsafe configuration: the electrical MIPI profile is enabled in the attachment-probe build."
}

& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M6 MIPI attachment-probe build failed."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
$elfPath = Join-Path $buildPath "micronux_m3_loader.elf"
$probeDisassembly = (& riscv32-esp-elf-objdump -d `
    --disassemble=micronux_mipi_dsi_prepare $elfPath | Out-String)
if ($probeDisassembly -notmatch "<probe_display_adapter>") {
    throw "Attachment-probe image does not route the MIPI gate to the read-only probe."
}
if ($probeDisassembly -match "esp_ldo_acquire_channel|esp_lcd_new_dsi_bus|create_selected_panel") {
    throw "Unsafe attachment-probe image contains a powered MIPI call in its active gate."
}
$image = Get-Item -LiteralPath $imagePath
$digest = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "M6 MIPI attachment probe: image=$($image.Length)B sha256=$digest dphy=off writes=0"

if ($Flash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M6 MIPI attachment-probe flash failed."
    }
    Write-Host "M6 MIPI attachment probe flashed: port=$Port confirmation=attachment-probe"
    Write-Host "Validate with scripts\m6-combined-test.py --expect-mipi-adapter present|absent."
} else {
    Write-Host "Build only. Nothing was flashed; no display command, rail, D-PHY, or backlight was touched."
}
