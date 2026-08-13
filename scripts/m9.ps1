[CmdletBinding()]
param(
    [string]$Port = "COM13",
    [switch]$SkipLinuxBuild,
    [switch]$Flash,
    [switch]$ConfirmExactKitC
)

$ErrorActionPreference = "Stop"

function Copy-FlashReadbackRange {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InputPath,
        [Parameter(Mandatory = $true)]
        [long]$Offset,
        [Parameter(Mandatory = $true)]
        [long]$Length,
        [Parameter(Mandatory = $true)]
        [string]$OutputPath
    )

    if ($Offset -lt 0 -or $Length -le 0) {
        throw "Invalid flash readback range: offset=$Offset length=$Length"
    }

    $inputStream = [System.IO.File]::OpenRead($InputPath)
    $outputStream = $null
    try {
        if ($Offset + $Length -gt $inputStream.Length) {
            throw "Flash readback range exceeds '$InputPath'."
        }
        [void]$inputStream.Seek($Offset, [System.IO.SeekOrigin]::Begin)
        $outputStream = [System.IO.File]::Open(
            $OutputPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $buffer = [byte[]]::new(1MB)
        $remaining = $Length
        while ($remaining -gt 0) {
            $requested = [int][System.Math]::Min($buffer.Length, $remaining)
            $received = $inputStream.Read($buffer, 0, $requested)
            if ($received -le 0) {
                throw "Flash readback ended before '$OutputPath' was complete."
            }
            $outputStream.Write($buffer, 0, $received)
            $remaining -= $received
        }
    }
    finally {
        if ($null -ne $outputStream) {
            $outputStream.Dispose()
        }
        $inputStream.Dispose()
    }

    if ((Get-Item -LiteralPath $OutputPath).Length -ne $Length) {
        throw "Extracted flash readback has the wrong length: $OutputPath"
    }
}

if ($Flash -and -not $ConfirmExactKitC) {
    throw "Flashing M9 requires -ConfirmExactKitC."
}

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslRepoPath = (& wsl.exe -- wslpath -a -u $repoPathArgument | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

if (-not $SkipLinuxBuild) {
    & wsl.exe -- env MICRONUX_JOBS=16 bash "$wslRepoPath/scripts/m9-build.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "MicroNUX M9 Linux input and presentation build failed."
    }
}

$artifactPath = Join-Path $repoPath "out\m9"
$loaderPath = Join-Path $repoPath "loader"
$idfPath = Join-Path $repoPath "build\idf-v6.0.1-m7"
$loaderBuildPath = Join-Path $repoPath "build\loader-m9-cold-jd9365-abi3"
$linuxImagePath = Join-Path $artifactPath "Image"
$dtbPath = Join-Path $artifactPath "esp32p4-micronux.dtb"
$metadataPath = Join-Path $artifactPath "metadata.bin"
$displayTestPath = Join-Path $artifactPath "micronux-display-test"
$rootfsPath = Join-Path $artifactPath "rootfs.cpio"
$linuxConfigPath = Join-Path $artifactPath "linux.config"
$vmlinuxPath = Join-Path $artifactPath "vmlinux"
$sha256SumsPath = Join-Path $artifactPath "SHA256SUMS"
$sourceContractPath = Join-Path $artifactPath "SOURCE-CONTRACT"
foreach ($artifact in @(
    $linuxImagePath,
    $dtbPath,
    $metadataPath,
    $displayTestPath,
    $rootfsPath,
    $linuxConfigPath,
    $vmlinuxPath,
    $sha256SumsPath,
    $sourceContractPath
)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Missing M9 artifact: $artifact"
    }
}
if ((Get-Item -LiteralPath $linuxImagePath).Length -gt 0x600000) {
    throw "M9 Image exceeds the 6 MiB Linux partition."
}
if ((Get-Item -LiteralPath $dtbPath).Length -gt 0x200000) {
    throw "M9 DTB exceeds the 2 MiB DTB partition."
}

$metadataBytes = [System.IO.File]::ReadAllBytes($metadataPath)
if ($metadataBytes.Length -ne 128) {
    throw "M9 metadata manifest must be exactly 128 bytes."
}
$metadataMagic = [BitConverter]::ToUInt32($metadataBytes, 0)
$metadataAbi = [BitConverter]::ToUInt32($metadataBytes, 4)
$metadataSize = [BitConverter]::ToUInt32($metadataBytes, 8)
$metadataFlags = [BitConverter]::ToUInt32($metadataBytes, 12)
$metadataKernelLoad = [BitConverter]::ToUInt32($metadataBytes, 16)
$metadataKernelFileSize = [BitConverter]::ToUInt32($metadataBytes, 20)
$metadataKernelMemorySize = [BitConverter]::ToUInt32($metadataBytes, 24)
$metadataDtbSize = [BitConverter]::ToUInt32($metadataBytes, 28)
$linuxImageSize = [uint64](Get-Item -LiteralPath $linuxImagePath).Length
$dtbSize = [uint64](Get-Item -LiteralPath $dtbPath).Length
if ($metadataMagic -ne 0x33584E4D -or
    $metadataAbi -ne 1 -or
    $metadataSize -ne 128 -or
    $metadataFlags -ne 1 -or
    $metadataKernelLoad -ne 0x48400000 -or
    $metadataKernelFileSize -ne $linuxImageSize -or
    $metadataKernelMemorySize -lt $metadataKernelFileSize -or
    $metadataDtbSize -ne $dtbSize) {
    throw "M9 metadata manifest does not match the packaged Image/DTB contract."
}
$metadataKernelEnd = [uint64]$metadataKernelLoad +
    [uint64]$metadataKernelMemorySize
if ($metadataKernelEnd -gt [uint64]0x49300000) {
    throw ("M9 metadata kernel memory span overlaps the display pool: " +
        "end=0x{0:x8} pool=0x49300000" -f $metadataKernelEnd)
}

$contractOutput = (& wsl.exe -- bash "$wslRepoPath/scripts/m9-build.sh" --print-contract |
    Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Could not calculate the current M9 Linux source contract."
}
$contractMatch = [regex]::Match(
    $contractOutput,
    '(?m)^MICRONUX:M9:SOURCE-CONTRACT sha256=([0-9a-f]{64})\r?$'
)
if (-not $contractMatch.Success) {
    throw "The M9 Linux source contract output was malformed."
}
$currentSourceContract = $contractMatch.Groups[1].Value
$artifactSourceContract = (Get-Content -LiteralPath $sourceContractPath -Raw).Trim()
if ($artifactSourceContract -ne $currentSourceContract) {
    throw "M9 Linux artifacts are stale for the current source contract. Re-run without -SkipLinuxBuild."
}

$artifactFiles = [ordered]@{
    "Image" = $linuxImagePath
    "esp32p4-micronux.dtb" = $dtbPath
    "metadata.bin" = $metadataPath
    "rootfs.cpio" = $rootfsPath
    "linux.config" = $linuxConfigPath
    "vmlinux" = $vmlinuxPath
    "micronux-display-test" = $displayTestPath
}
$expectedDigests = @{}
foreach ($line in Get-Content -LiteralPath $sha256SumsPath) {
    if ($line -notmatch '^([0-9a-f]{64})  ([^/\\]+)$') {
        throw "Malformed M9 artifact checksum line: '$line'"
    }
    $name = $Matches[2]
    if ($expectedDigests.ContainsKey($name)) {
        throw "Duplicate M9 artifact checksum: $name"
    }
    $expectedDigests[$name] = $Matches[1]
}
if ($expectedDigests.Count -ne $artifactFiles.Count) {
    throw "M9 artifact checksum manifest does not contain exactly $($artifactFiles.Count) entries."
}
foreach ($entry in $artifactFiles.GetEnumerator()) {
    if (-not $expectedDigests.ContainsKey($entry.Key)) {
        throw "M9 artifact checksum manifest is missing '$($entry.Key)'."
    }
    $actualDigest = (Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualDigest -ne $expectedDigests[$entry.Key]) {
        throw "M9 artifact checksum mismatch: $($entry.Key)"
    }
}

& (Join-Path $PSScriptRoot "m7-prepare-idf.ps1") -WorktreePath $idfPath

$previousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& (Join-Path $idfPath "export.ps1") *> $null
$exportExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorPreference
if ($exportExitCode -ne 0) {
    throw "Task-local ESP-IDF v6.0.1 activation failed."
}
if ([System.IO.Path]::GetFullPath($env:IDF_PATH) -ne
    [System.IO.Path]::GetFullPath($idfPath)) {
    throw "ESP-IDF activation selected '$env:IDF_PATH' instead of '$idfPath'."
}
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
$idfPy = Join-Path $idfPath "tools\idf.py"
if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf) -or
    -not (Test-Path -LiteralPath $idfPy -PathType Leaf)) {
    throw "Task-local ESP-IDF Python entrypoint is unavailable."
}
$idfVersion = (& $idfPython $idfPy --version |
    Out-String).Trim()
if ($idfVersion -ne "ESP-IDF v6.0.1-dirty") {
    throw "Expected the verified patched ESP-IDF v6.0.1 worktree, got '$idfVersion'."
}

$env:CMAKE_GENERATOR = "Ninja"
$env:IDF_CCACHE_ENABLE = "1"
$sdkconfigPath = Join-Path $loaderBuildPath "sdkconfig"
$sdkconfigDefaults = @(
    (Join-Path $loaderPath "sdkconfig.defaults"),
    (Join-Path $loaderPath "sdkconfig.combined.defaults"),
    (Join-Path $loaderPath "sdkconfig.m9.defaults")
) -join ";"

Remove-Item -LiteralPath $sdkconfigPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$sdkconfigPath.old" -Force -ErrorAction SilentlyContinue

& $idfPython $idfPy -C $loaderPath -B $loaderBuildPath `
    -D "SDKCONFIG=$sdkconfigPath" `
    -D "SDKCONFIG_DEFAULTS=$sdkconfigDefaults" reconfigure
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader configure failed."
}
$configuration = Get-Content -LiteralPath $sdkconfigPath -Raw
foreach ($required in @(
    "CONFIG_MICRONUX_M9_JD9365_COLD_RELINQUISH=y",
    "CONFIG_MICRONUX_M7_EARLY_UMODE_DENY=y",
    "CONFIG_MICRONUX_C6_SDIO_PROFILE=y",
    "CONFIG_MICRONUX_SDMMC_DUAL_SLOT_PROFILE=y",
    "CONFIG_SPIRAM_SPEED_200M=y",
    "CONFIG_SPIRAM_XIP_FROM_PSRAM=y",
    "CONFIG_CACHE_L2_CACHE_256KB=y",
    "CONFIG_CACHE_L2_CACHE_LINE_64B=y",
    "CONFIG_COMPILER_OPTIMIZATION_PERF=y"
)) {
    if ($configuration -notmatch "(?m)^$([regex]::Escape($required))\r?$") {
        throw "M9 loader configuration is missing '$required'."
    }
}
foreach ($forbidden in @(
    "CONFIG_MICRONUX_MIPI_DSI=y",
    "CONFIG_MICRONUX_MIPI_DSI_PROBE=y",
    "CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280=y"
)) {
    if ($configuration -match "(?m)^$([regex]::Escape($forbidden))\r?$") {
        throw "M9 cold loader configuration unexpectedly contains '$forbidden'."
    }
}

& ninja -C $loaderBuildPath -j 16
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader build failed."
}
$loaderImagePath = Join-Path $loaderBuildPath "micronux_m3_loader.bin"
$loaderElfPath = Join-Path $loaderBuildPath "micronux_m3_loader.elf"
foreach ($loaderArtifact in @($loaderImagePath, $loaderElfPath)) {
    if (-not (Test-Path -LiteralPath $loaderArtifact -PathType Leaf)) {
        throw "Missing M9 loader artifact: $loaderArtifact"
    }
}
$loaderStrings = (& riscv32-esp-elf-strings $loaderElfPath | Out-String)
foreach ($marker in @(
    "MICRONUX:M9.2:COLD-EXTERNAL state=ready pwm-zero-write=acked reset-prepare-write=acked pwm-zero-settle=elapsed reset-assert-write=acked reset-hold=elapsed i2c=released",
    "MICRONUX:M9.2:COLD-MEMORY state=ready",
    "desc_bytes=%lu desc_page_bytes=%lu descriptors=zero",
    "MICRONUX:M9.2:COLD-PREPARE state=ready abi=3 panel=jd9365",
    "display-init=none splash=none source=none gdma=quiesced",
    "MICRONUX:M9.2:COLD-HANDOFF state=ready contract=invalid route=pending dma-pms=pending i2c=released",
    "MICRONUX:M9.2:COLD-STAGE state=ready abi=3",
    "flags=0x%08lx route=ready dma-pms=ready",
    "panel-payload-crc=cea07f9b contract=invalid"
)) {
    if ($loaderStrings -notmatch [regex]::Escape($marker)) {
        throw "M9 loader is missing marker '$marker'."
    }
}
foreach ($forbiddenMarker in @(
    "MICRONUX:M9.2:COLD-EXTERNAL state=ready pwm=0 gate=off reset=asserted",
    "MICRONUX:M9.2:COLD-PUBLISH state=ready abi=3",
    "profile-crc=linux-validates",
    "MICRONUX:M9:PANEL-RESET state=ready",
    "MICRONUX:M9.2:DSI-HANDOFF-SOURCE state=ready",
    "MICRONUX:M9.2:DSI-HANDOFF state=ready abi=2",
    "MICRONUX:M9.2:LINK-POLICY state=ready"
)) {
    if ($loaderStrings -match [regex]::Escape($forbiddenMarker)) {
        throw "M9 cold loader unexpectedly contains live-display marker '$forbiddenMarker'."
    }
}
$loaderDigest = (Get-FileHash -LiteralPath $loaderImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "M9 loader built: image=$((Get-Item -LiteralPath $loaderImagePath).Length)B sha256=$loaderDigest idf=v6.0.1 panel=jd9365 mode=cold-relinquish abi=3"

if (-not $Flash) {
    Write-Host "M9 Linux and loader build validation passed. Nothing was flashed."
    exit 0
}

& $idfPython (Join-Path $PSScriptRoot "usb-reset-arm.py") --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the MicroNUX USB reset path for flashing."
}

& $idfPython $idfPy -C $loaderPath -B $loaderBuildPath -p $Port flash
if ($LASTEXITCODE -ne 0) {
    throw "M9 loader flash failed."
}

& $idfPython (Join-Path $PSScriptRoot "usb-reset-arm.py") --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Could not re-arm the MicroNUX USB reset path after loader flash."
}

& $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
    --before default-reset --after hard-reset write-flash `
    0x200000 $linuxImagePath `
    0x800000 $dtbPath `
    0xA00000 $metadataPath
if ($LASTEXITCODE -ne 0) {
    throw "M9 Linux payload flash failed."
}

$flashArtifacts = @(
    [pscustomobject]@{
        Name = "loader"
        Address = [long]0x10000
        Path = $loaderImagePath
    },
    [pscustomobject]@{
        Name = "Image"
        Address = [long]0x200000
        Path = $linuxImagePath
    },
    [pscustomobject]@{
        Name = "esp32p4-micronux.dtb"
        Address = [long]0x800000
        Path = $dtbPath
    },
    [pscustomobject]@{
        Name = "metadata.bin"
        Address = [long]0xA00000
        Path = $metadataPath
    }
)
$flashReadStart = [long](
    $flashArtifacts | Measure-Object -Property Address -Minimum
).Minimum
$flashReadEnd = [long]($flashArtifacts | ForEach-Object {
    $_.Address + (Get-Item -LiteralPath $_.Path).Length
} | Measure-Object -Maximum).Maximum
$flashReadLength = $flashReadEnd - $flashReadStart
$readbackRoot = Join-Path $repoPath "build\m9-readback"
$readbackRun = "{0}-{1}" -f (
    [DateTime]::UtcNow.ToString(
        "yyyyMMddTHHmmssfffZ",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$readbackPath = Join-Path $readbackRoot $readbackRun
[void](New-Item -ItemType Directory -Path $readbackPath)
$flashReadbackPath = Join-Path $readbackPath "flash-0x00010000-through-metadata.bin"

& $idfPython (Join-Path $PSScriptRoot "usb-reset-arm.py") --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Could not re-arm the MicroNUX USB reset path for flash readback."
}

& $idfPython -m esptool --chip esp32p4 -p $Port -b 921600 `
    --before default-reset --after hard-reset read-flash `
    ("0x{0:x}" -f $flashReadStart) `
    ("0x{0:x}" -f $flashReadLength) `
    $flashReadbackPath
if ($LASTEXITCODE -ne 0) {
    throw "M9 flash readback failed. Evidence directory: $readbackPath"
}
if ((Get-Item -LiteralPath $flashReadbackPath).Length -ne $flashReadLength) {
    throw "M9 flash readback length mismatch. Evidence directory: $readbackPath"
}

$readbackRecords = @()
foreach ($flashArtifact in $flashArtifacts) {
    $artifactLength = (Get-Item -LiteralPath $flashArtifact.Path).Length
    $artifactReadbackPath = Join-Path $readbackPath ($flashArtifact.Name + ".readback")
    Copy-FlashReadbackRange `
        -InputPath $flashReadbackPath `
        -Offset ($flashArtifact.Address - $flashReadStart) `
        -Length $artifactLength `
        -OutputPath $artifactReadbackPath
    $hostDigest = (Get-FileHash -LiteralPath $flashArtifact.Path -Algorithm SHA256).Hash.ToLowerInvariant()
    $readbackDigest = (Get-FileHash -LiteralPath $artifactReadbackPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $readbackRecords += [pscustomobject]@{
        name = $flashArtifact.Name
        address = "0x{0:x}" -f $flashArtifact.Address
        length = $artifactLength
        host_sha256 = $hostDigest
        readback_sha256 = $readbackDigest
        match = ($hostDigest -eq $readbackDigest)
    }
}
$readbackManifestPath = Join-Path $readbackPath "readback.json"
$readbackManifest = $readbackRecords | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $readbackManifestPath,
    $readbackManifest + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)
$mismatches = @($readbackRecords | Where-Object { -not $_.match })
if ($mismatches.Count -ne 0) {
    $names = ($mismatches | ForEach-Object { $_.name }) -join ","
    throw "M9 flash readback hash mismatch: $names. Evidence directory: $readbackPath"
}

Write-Host ((
        "MICRONUX:M9.2:FLASH-READBACK state=pass port={0} " +
        "artifacts=4 loader={1} image={2} dtb={3} metadata={4} evidence={5}"
    ) -f $Port,
        $readbackRecords[0].readback_sha256,
        $readbackRecords[1].readback_sha256,
        $readbackRecords[2].readback_sha256,
        $readbackRecords[3].readback_sha256,
        $readbackPath)
Write-Host "M9 loader and Linux payload flashed and read back: port=$Port display=native-cold-abi3-800x1280 diagnostic=installed vpg=unsupported touch=async-after-reveal"
