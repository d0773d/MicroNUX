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
    $profileDefaults
) -join ";"

& idf.py -C $loaderPath -B $buildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M6 MIPI-DSI loader configure failed for '$profile'."
}
& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M6 MIPI-DSI loader build failed for '$profile'."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
$image = Get-Item -LiteralPath $imagePath
$digest = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "M6 MIPI-DSI build: panel=$profile image=$($image.Length)B sha256=$digest"

if ($Flash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M6 MIPI-DSI loader flash failed for '$profile'."
    }
    Write-Host "M6 MIPI-DSI flashed: panel=$profile port=$Port confirmation=exact-panel"
} else {
    Write-Host "Build only. Nothing was flashed and no display rail was powered."
}
