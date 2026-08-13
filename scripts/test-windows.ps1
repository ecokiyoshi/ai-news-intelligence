[CmdletBinding()]
param(
    [string]$Python,
    [string[]]$PytestArgs = @("-q")
)

$ErrorActionPreference = "Stop"
$repository = Split-Path -Parent $PSScriptRoot

$bashCandidates = @(
    $env:AI_NEWS_BASH,
    "C:\Program Files\Git\bin\bash.exe",
    "C:\Program Files\Git\usr\bin\bash.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
if (-not $bashCandidates) {
    throw "Git Bash was not found. Install Git for Windows or set AI_NEWS_BASH."
}
$env:AI_NEWS_BASH = $bashCandidates[0]

if (-not $Python) {
    $localPython = Join-Path $repository ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $localPython) {
        & $localPython -c "import pytest" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $Python = $localPython
        }
    }
    if (-not $Python) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $Python = $pythonCommand.Source
        }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "A Python installation with pytest is required. Pass -Python C:\path\python.exe."
}

& $Python -c "import pytest" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python does not have pytest installed: $Python"
}

Write-Host "Python: $Python"
Write-Host "Bash:   $env:AI_NEWS_BASH"
Push-Location $repository
try {
    & $Python -m pytest @PytestArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
