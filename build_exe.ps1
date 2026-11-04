$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$pythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $pythonPath = $pythonCommand.Source
}

Write-Host "Using Python: $pythonPath"
Write-Host "Running tests..."
& $pythonPath -m pytest -q --basetemp .pytest-tmp
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed; build stopped."
}

Write-Host "Building Inspection Reporting onedir package..."
& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name InspectionReporting `
    --contents-directory . `
    --add-data "templates;templates" `
    --add-data "static;static" `
    app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "dist\InspectionReporting\instance\backups") | Out-Null

Write-Host ""
Write-Host "Build complete: $PSScriptRoot\dist\InspectionReporting"
Write-Host "Run: .\dist\InspectionReporting\InspectionReporting.exe"
