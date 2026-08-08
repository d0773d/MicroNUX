[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [ValidateRange(1, 20)]
    [int]$Boots = 3,
    [switch]$SkipLinuxBuild,
    [switch]$SkipLoaderBuild,
    [switch]$SkipFlash
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslRepoPath = (& wsl.exe -- wslpath -a -u $repoPathArgument | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

if (-not $SkipLinuxBuild) {
    & wsl.exe -- env MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m6-network-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M6 network Linux build failed."
    }
}

$artifactPath = Join-Path $repoPath "out\m6-network"
$imagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
foreach ($artifact in @($imagePath, $dtbPath, $metadataPath)) {
    if (-not (Test-Path -LiteralPath $artifact)) {
        throw "Missing M6 network artifact: $artifact"
    }
}
if ((Get-Item -LiteralPath $imagePath).Length -gt 0x600000) {
    throw "M6 network Image exceeds the 6 MiB Linux partition."
}
if ((Get-Item -LiteralPath $dtbPath).Length -gt 0x200000) {
    throw "M6 network DTB exceeds the 2 MiB DTB partition."
}

$idfPath = "C:\esp\v6.0.1\esp-idf"
$loaderPath = Join-Path $repoPath "loader"
$buildPath = Join-Path $repoPath "build\m6-network"
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
$idfPython = (Get-Command python -ErrorAction Stop).Source

if (-not $SkipLoaderBuild) {
    $env:CMAKE_GENERATOR = "Ninja"
    $env:IDF_CCACHE_ENABLE = "1"
    $sdkconfigPath = Join-Path $buildPath "sdkconfig"
    $sdkconfigDefaults = @(
        (Join-Path $loaderPath "sdkconfig.defaults"),
        (Join-Path $loaderPath "sdkconfig.network.defaults")
    ) -join ";"
    & idf.py -C $loaderPath -B $buildPath `
        -D "SDKCONFIG=$sdkconfigPath" `
        -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
    if ($LASTEXITCODE -ne 0) {
        throw "M6 network loader configure failed."
    }
    & ninja -C $buildPath -j 16
    if ($LASTEXITCODE -ne 0) {
        throw "M6 network loader build failed."
    }
}

if (-not $SkipFlash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M6 network loader flash failed."
    }

    & $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
        --before default-reset --after hard-reset write-flash `
        0x200000 $imagePath `
        0x800000 $dtbPath `
        0xA00000 $metadataPath
    if ($LASTEXITCODE -ne 0) {
        throw "M6 network Linux payload flash failed."
    }
}

& $idfPython (Join-Path $PSScriptRoot "m6-network-test.py") `
    --port $Port --boots $Boots --artifact-dir $artifactPath
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M6 C6 SDIO enumeration test failed."
}
