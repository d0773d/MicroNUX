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
    & wsl.exe -- env MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m3-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M3 Linux build failed."
    }
}

$imagePath = Join-Path $repoPath "out\m3\Image"
$dtbPath = Join-Path $repoPath "out\m3\esp32p4-micronux.dtb"
$metadataPath = Join-Path $repoPath "out\m3\metadata.bin"
foreach ($artifact in @($imagePath, $dtbPath, $metadataPath)) {
    if (-not (Test-Path -LiteralPath $artifact)) {
        throw "Missing M3 artifact: $artifact"
    }
}

$idfPath = "C:\esp\v6.0.1\esp-idf"
$loaderPath = Join-Path $repoPath "loader"
$buildPath = Join-Path $repoPath "build\m3"
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
    $sdkconfigPath = Join-Path $buildPath "sdkconfig"
    & idf.py -C $loaderPath -B $buildPath -D "SDKCONFIG=$sdkconfigPath" reconfigure
    if ($LASTEXITCODE -ne 0) {
        throw "M3 loader configure failed."
    }
    & ninja -C $buildPath -j 16
    if ($LASTEXITCODE -ne 0) {
        throw "M3 loader build failed."
    }
}

if (-not $SkipFlash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M3 loader flash failed."
    }

    & $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
        --before default-reset --after hard-reset write-flash `
        0x200000 $imagePath `
        0x800000 $dtbPath `
        0xA00000 $metadataPath
    if ($LASTEXITCODE -ne 0) {
        throw "M3 Linux payload flash failed."
    }
}

& $idfPython (Join-Path $PSScriptRoot "m3-test.py") --port $Port --boots $Boots
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M3 hardware test failed."
}
