[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [ValidateRange(1, 20)]
    [int]$Boots = 3,
    [switch]$SkipLinuxBuild,
    [switch]$SkipLoaderBuild,
    [switch]$SkipFlash,
    [switch]$ControllerOnly
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslRepoPath = (& wsl.exe -- wslpath -a -u $repoPathArgument | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

if (-not $SkipLinuxBuild) {
    & wsl.exe -- env MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m6-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M6 Linux build failed."
    }
}

$artifactPath = Join-Path $repoPath "out\m6"
$imagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
$selftestPath = Join-Path $artifactPath "micronux-selftest"
$storageTestPath = Join-Path $artifactPath "micronux-storage-test"
foreach ($artifact in @($imagePath, $dtbPath, $metadataPath, $selftestPath, $storageTestPath)) {
    if (-not (Test-Path -LiteralPath $artifact)) {
        throw "Missing M6 artifact: $artifact"
    }
}

$idfPath = "C:\esp\v6.0.1\esp-idf"
$loaderPath = Join-Path $repoPath "loader"
$buildPath = Join-Path $repoPath "build\m6"
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
        throw "M6 loader configure failed."
    }
    & ninja -C $buildPath -j 16
    if ($LASTEXITCODE -ne 0) {
        throw "M6 loader build failed."
    }
}

if (-not $SkipFlash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M6 loader flash failed."
    }

    & $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
        --before default-reset --after hard-reset write-flash `
        0x200000 $imagePath `
        0x800000 $dtbPath `
        0xA00000 $metadataPath
    if ($LASTEXITCODE -ne 0) {
        throw "M6 Linux payload flash failed."
    }
}

$testArguments = @(
    (Join-Path $PSScriptRoot "m6-test.py"),
    "--port", $Port,
    "--boots", $Boots,
    "--artifact-dir", $artifactPath
)
if ($ControllerOnly) {
    $testArguments += "--allow-no-card"
}
& $idfPython @testArguments
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M6 hardware test failed."
}
