[CmdletBinding()]
param(
    [string]$Port = "COM14"
)

$ErrorActionPreference = "Stop"
$idfPython = "C:\Espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython)) {
    throw "ESP-IDF v6.0.1 Python environment not found: $idfPython"
}

Write-Host "Opening the MicroNUX console on $Port. Press Ctrl+] to exit."
& $idfPython -m serial.tools.miniterm $Port 115200 --raw --eol LF --rts 0 --dtr 0
