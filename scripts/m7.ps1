[CmdletBinding()]
param(
    [string]$Port = "COM14",
    [int]$Boots = 3,
    [switch]$Flash,
    [switch]$Test,
    [switch]$ConfirmExactKitC,
    [switch]$ConfirmPmpChange
)

$ErrorActionPreference = "Stop"

if ($Boots -lt 1) {
    throw "Boots must be at least one."
}
if ($Test -and -not $Flash) {
    throw "-Test requires -Flash so the tested loader is the image just built."
}
if ($Flash -and (-not $ConfirmExactKitC -or -not $ConfirmPmpChange)) {
    throw "Flashing M7 requires -ConfirmExactKitC and -ConfirmPmpChange."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$loaderPath = Join-Path $repoPath "loader"
$idfPath = Join-Path $repoPath "build\idf-v6.0.1-m7"
$buildPath = Join-Path $repoPath "build\loader-m7-early-deny-patched"
$artifactPath = Join-Path $repoPath "out\m8"
$logPath = Join-Path $artifactPath "m7-early-deny-three-boot.log"

& (Join-Path $PSScriptRoot "m7-prepare-idf.ps1") -WorktreePath $idfPath

$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& (Join-Path $idfPath "export.ps1") *> $null
$exportExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorPreference
if ($exportExitCode -ne 0) {
    throw "Task-local ESP-IDF v6.0.1 activation failed."
}
if ([System.IO.Path]::GetFullPath($env:IDF_PATH) -ne [System.IO.Path]::GetFullPath($idfPath)) {
    throw "ESP-IDF activation selected '$env:IDF_PATH' instead of '$idfPath'."
}
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
$idfPy = Join-Path $idfPath "tools\idf.py"
if (-not (Test-Path -LiteralPath $idfPython) -or
    -not (Test-Path -LiteralPath $idfPy)) {
    throw "Task-local ESP-IDF Python entrypoint is unavailable."
}
# The user's PowerShell profile defines an idf.py function pinned to the global
# SDK. Invoke the task-local script by absolute path so that function cannot
# silently replace the verified checkout.
$idfVersion = (& $idfPython $idfPy --version | Out-String).Trim()
if ($idfVersion -ne "ESP-IDF v6.0.1-dirty") {
    throw "Expected the verified patched ESP-IDF v6.0.1 worktree, got '$idfVersion'."
}

$env:CMAKE_GENERATOR = "Ninja"
$env:IDF_CCACHE_ENABLE = "1"
$sdkconfigPath = Join-Path $buildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.combined.defaults"),
    (Join-Path $loaderPath "sdkconfig.mipi-jd9365.defaults"),
    (Join-Path $loaderPath "sdkconfig.m7.defaults")
) -join ";"

& $idfPython $idfPy -C $loaderPath -B $buildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M7 loader configure failed."
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
        throw "M7 generated configuration is missing '$required'."
    }
}

& ninja -C $buildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M7 loader build failed."
}

$imagePath = Join-Path $buildPath "micronux_m3_loader.bin"
$elfPath = Join-Path $buildPath "micronux_m3_loader.elf"
$patchedSourcePath = [System.IO.Path]::GetFullPath(
    (Join-Path $idfPath "components\esp_hw_support\port\esp32p4\cpu_region_protect.c")
)
foreach ($compileDatabasePath in @(
    (Join-Path $buildPath "compile_commands.json"),
    (Join-Path $buildPath "bootloader\compile_commands.json")
)) {
    $compileDatabase = Get-Content -LiteralPath $compileDatabasePath -Raw | ConvertFrom-Json
    $patchedSourceSeen = $false
    foreach ($compileCommand in $compileDatabase) {
        if ([System.IO.Path]::GetFullPath($compileCommand.file) -eq $patchedSourcePath) {
            $patchedSourceSeen = $true
            break
        }
    }
    if (-not $patchedSourceSeen) {
        throw "Compile database '$compileDatabasePath' does not reference the verified patched ESP-IDF source."
    }
}
$image = Get-Item -LiteralPath $imagePath
$digest = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
$strings = (& riscv32-esp-elf-strings $elfPath | Out-String)
if ($strings -notmatch "MICRONUX:M7:PMP baseline=pass early-deny=pass" -or
    $strings -notmatch "MICRONUX:M7:PMP-AUDIT state=fail") {
    throw "M7 PMP audit markers are missing from the linked loader."
}
Write-Host "M7 loader built: image=$($image.Length)B sha256=$digest idf=v6.0.1 early-deny=tracked kit-c=jd9365"

if (-not $Flash) {
    Write-Host "Build only. Nothing was flashed."
    exit 0
}

& $idfPython $idfPy -C $loaderPath -B $buildPath -p $Port flash
if ($LASTEXITCODE -ne 0) {
    throw "M7 loader flash failed."
}
Write-Host "M7 loader flashed: port=$Port pmp=early-deny kit-c=confirmed"

if ($Test) {
    & $idfPython (Join-Path $PSScriptRoot "m7-baseline-test.py") `
        --port $Port --boots $Boots --timeout 180 `
        --pmp-profile early-deny --artifact-dir $artifactPath `
        --loader-image $imagePath --log $logPath
    if ($LASTEXITCODE -ne 0) {
        throw "M7 early-deny hardware gate failed."
    }
}
