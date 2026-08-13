[CmdletBinding()]
param(
    [string]$Port = "COM13",
    [ValidateRange(1, 20)]
    [int]$Boots = 3,
    [switch]$SkipBuild,
    [switch]$SkipFlash
)

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$buildPath = Join-Path $repoPath "build\m2"
$idfPath = "C:\esp\v6.0.1\esp-idf"
$expectedImageSha256 = "f42ac72f6f24027dfa344fc96ce840a0aabce24da8a291882a8b27f74b222587"
$linuxImagePath = "/home/$(& wsl.exe -- sh -lc 'printf %s "$USER"')/.cache/micronux/m1/output-2025.02.16-a91c21807653/images/Image"
$windowsImagePath = (& wsl.exe -- wslpath -w $linuxImagePath | Out-String).Trim()

if ([string]::IsNullOrWhiteSpace($windowsImagePath) -or -not (Test-Path -LiteralPath $windowsImagePath)) {
    throw "The pinned M1 Linux Image was not found. Run .\scripts\m1.ps1 first."
}

$actualImageSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $windowsImagePath).Hash.ToLowerInvariant()
if ($actualImageSha256 -ne $expectedImageSha256) {
    throw "M1 Linux Image checksum mismatch: $actualImageSha256"
}

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

if (-not $SkipBuild) {
    $env:CMAKE_GENERATOR = "Ninja"
    & idf.py -C $loaderPath -B $buildPath reconfigure
    if ($LASTEXITCODE -ne 0) {
        throw "M2 loader configure failed."
    }
    & ninja -C $buildPath -j 16
    if ($LASTEXITCODE -ne 0) {
        throw "M2 loader build failed."
    }
}

if (-not $SkipFlash) {
    & idf.py -C $loaderPath -B $buildPath -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "M2 loader flash failed."
    }

    & python -m esptool --chip esp32p4 -p $Port -b 921600 `
        --before default-reset --after hard-reset `
        write-flash 0x200000 $windowsImagePath
    if ($LASTEXITCODE -ne 0) {
        throw "M1 Linux Image flash failed."
    }
}

& python (Join-Path $PSScriptRoot "m2-test.py") --port $Port --boots $Boots
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M2 hardware test failed."
}
