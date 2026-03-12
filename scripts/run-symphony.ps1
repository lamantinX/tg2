param(
    [string]$SymphonyRoot,
    [int]$Port = 8787,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ProjectsRoot = Split-Path -Parent $RepoRoot
if (-not $SymphonyRoot) {
    $SymphonyRoot = Join-Path $ProjectsRoot 'symphony'
}

$WorkflowPath = Join-Path $RepoRoot 'WORKFLOW.md'
$EnvPath = Join-Path $RepoRoot '.env.symphony'
$GitBashPath = 'C:\Program Files\Git\bin'

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Import-EnvFile {
    param([string]$Path)

    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            return
        }
        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) {
            return
        }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Test-PythonModule {
    param(
        [string]$PythonExe,
        [string]$ModuleName
    )

    $previous = $global:PSNativeCommandUseErrorActionPreference
    $global:PSNativeCommandUseErrorActionPreference = $false
    try {
        & $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)" 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $global:PSNativeCommandUseErrorActionPreference = $previous
    }
}

if (-not (Test-Path $WorkflowPath)) {
    throw "WORKFLOW.md not found: $WorkflowPath"
}
if (-not (Test-Path $EnvPath)) {
    throw ".env.symphony not found: $EnvPath"
}
if (-not (Test-Path $SymphonyRoot)) {
    throw "Symphony repo not found: $SymphonyRoot"
}

$workflowContent = Get-Content $WorkflowPath -Raw
if (($workflowContent -match 'replace-with-your-linear-project-slug') -or ($workflowContent -match 'project_slug:\s*["'']?your-project-slug["'']?')) {
    throw "Set tracker.project_slug in WORKFLOW.md before starting Symphony."
}

Write-Step "Loading environment from .env.symphony"
Import-EnvFile -Path $EnvPath

if (-not $env:CODEX_BIN) {
    $env:CODEX_BIN = 'codex.exe'
}

if ((Test-Path $GitBashPath) -and ($env:PATH -notlike "$GitBashPath*")) {
    $env:PATH = "$GitBashPath;$env:PATH"
}

$PythonCommand = Get-Command python -ErrorAction Stop
$PythonExe = $PythonCommand.Source

if ($Install) {
    Write-Step 'Installing PyYAML into the current Python environment'
    & $PythonExe -m pip install PyYAML
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install PyYAML.'
    }
}

if (-not (Test-PythonModule -PythonExe $PythonExe -ModuleName 'yaml')) {
    throw "Python module 'yaml' is missing. Run .\\scripts\\run-symphony.cmd -Install or install PyYAML manually."
}

$existingPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
if ($existingPythonPath) {
    $env:PYTHONPATH = "$SymphonyRoot;$existingPythonPath"
} else {
    $env:PYTHONPATH = $SymphonyRoot
}

Write-Step "Starting Symphony from source"
Write-Host "Workflow: $WorkflowPath"
Write-Host "Symphony: $SymphonyRoot"
Write-Host "Python: $PythonExe"
Write-Host "Codex: $env:CODEX_BIN"
Write-Host ""

Push-Location $SymphonyRoot
try {
    & $PythonExe -m symphony.cli $WorkflowPath --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
