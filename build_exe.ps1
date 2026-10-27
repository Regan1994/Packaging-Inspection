$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Running tests..."
python -m pytest -q --basetemp .pytest-tmp

Write-Host "Building Inspection Reporting onedir package..."
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name InspectionReporting `
    --contents-directory . `
    --add-data "templates;templates" `
    --add-data "static;static" `
    app.py

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "dist\InspectionReporting\instance\backups") | Out-Null

Write-Host ""
Write-Host "Build complete: $PSScriptRoot\dist\InspectionReporting"
Write-Host "Run: .\dist\InspectionReporting\InspectionReporting.exe"
