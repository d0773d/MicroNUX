[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [switch]$Flash,
    [switch]$ConfirmP4
)

$ErrorActionPreference = "Stop"

if ($Flash -and -not $ConfirmP4) {
    throw "Flashing requires both -Flash and -ConfirmP4. This command must target the ESP32-P4 USB port."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$buildPath = Join-Path $repoPath "build\m6-provisioning"
$idfPath = "C:\esp\v6.0.1\esp-idf"

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
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$sdkconfigPath = Join-Path $buildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.network.defaults"),
    (Join-Path $loaderPath "sdkconfig.provisioning.defaults")
) -join ";"

& idf.py -C $loaderPath -B $buildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" `
    -D "MICRONUX_PROVISIONING_BUILD=ON" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M6 provisioning loader configure failed."
}

$requiredConfig = @(
    "CONFIG_MICRONUX_C6_PROVISIONING=y",
    "CONFIG_ESP_WIFI_REMOTE_ENABLED=y",
    "CONFIG_ESP_WIFI_REMOTE_LIBRARY_HOSTED=y",
    "CONFIG_SLAVE_IDF_TARGET_ESP32C6=y",
    "CONFIG_ESP_HOSTED_P4_DEV_BOARD_FUNC_BOARD=y",
    "CONFIG_ESP_HOSTED_SDIO_HOST_INTERFACE=y",
    "CONFIG_ESP_HOSTED_SDIO_SLOT_1=y",
    "CONFIG_ESP_HOSTED_SDIO_4_BIT_BUS=y",
    "CONFIG_ESP_HOSTED_SDIO_CLOCK_FREQ_KHZ=20000",
    "CONFIG_ESP_HOSTED_SDIO_RESET_ACTIVE_HIGH=y",
    "CONFIG_ESP_HOSTED_SDIO_PIN_CLK=18",
    "CONFIG_ESP_HOSTED_SDIO_PIN_CMD=19",
    "CONFIG_ESP_HOSTED_SDIO_PIN_D0=14",
    "CONFIG_ESP_HOSTED_SDIO_PIN_D1=15",
    "CONFIG_ESP_HOSTED_SDIO_PIN_D2=16",
    "CONFIG_ESP_HOSTED_SDIO_PIN_D3=17",
    "CONFIG_ESP_HOSTED_GPIO_SLAVE_RESET_SLAVE=54",
    "CONFIG_ESP_HOSTED_SLAVE_RESET_ON_EVERY_HOST_BOOTUP=y",
    "CONFIG_BT_CONTROLLER_DISABLED=y",
    "CONFIG_ESP_HOSTED_ENABLE_BT_NIMBLE=y",
    "CONFIG_ESP_HOSTED_NIMBLE_HCI_VHCI=y",
    "CONFIG_NETWORK_PROV_NETWORK_TYPE_WIFI=y",
    "CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_2=y",
    "# CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_0 is not set",
    "# CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_1 is not set",
    "CONFIG_LOG_MAXIMUM_LEVEL=3"
)
$configured = Get-Content -LiteralPath $sdkconfigPath
foreach ($line in $requiredConfig) {
    if ($configured -notcontains $line) {
        throw "Provisioning profile mismatch: missing '$line'. Use a fresh build directory."
    }
}

& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M6 provisioning loader build failed."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
if (-not (Test-Path -LiteralPath $imagePath)) {
    throw "Missing P4 provisioning loader image: $imagePath"
}
$imageSize = (Get-Item -LiteralPath $imagePath).Length
if ($imageSize -gt 0x1F0000) {
    throw "P4 provisioning loader exceeds the 0x1F0000-byte factory partition."
}

Write-Host ("P4 provisioning loader built: {0} bytes; C6 firmware was not built or modified." -f $imageSize)

if ($Flash) {
    Write-Host "Flashing only the ESP32-P4 loader through $Port. The ESP32-C6 flash is out of scope."
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "P4 provisioning loader flash failed."
    }
}
