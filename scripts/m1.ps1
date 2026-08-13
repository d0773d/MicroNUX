[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPathArgument = $repoPath.Replace("\", "/")
$wslPathOutput = & wsl.exe -- wslpath -a -u $repoPathArgument
if ($LASTEXITCODE -ne 0 -or $null -eq $wslPathOutput) {
    throw "Could not map the MicroNUX repository into WSL."
}
$wslRepoPath = ($wslPathOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($wslRepoPath)) {
    throw "Could not map the MicroNUX repository into WSL."
}

& wsl.exe -- bash "$wslRepoPath/scripts/m1-build.sh"
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M1 build failed."
}

& wsl.exe -- python3 "$wslRepoPath/scripts/m1-test.py"
if ($LASTEXITCODE -ne 0) {
    throw "MicroNUX M1 QEMU test failed."
}
