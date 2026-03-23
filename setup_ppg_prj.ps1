# Setup PPG Project Environment - Part 2 (Environment exists)
# PowerShell script to install dependencies in ppg_prj environment

# Conda path and environment
$condaPath = 'D:\Anaconda\Scripts\conda.exe'
$envPath = 'D:\Anaconda\envs\ppg_prj'
$pythonPath = Join-Path $envPath 'python.exe'
$pipPath = Join-Path $envPath 'Scripts\pip.exe'

Write-Host "=== Using Python: $pythonPath ===" -ForegroundColor Cyan
Write-Host "Python Version:" -ForegroundColor Yellow
& $pythonPath --version

Write-Host "`n=== Step 1: Install project dependencies ===" -ForegroundColor Green
# Use default PyPI (no mirror, no proxy)
& $pipPath install pyqt5 pyqtgraph numpy pyserial --default-timeout=100

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n=== Step 2: Install MATLAB Engine API ===" -ForegroundColor Green
    $matlabEnginePath = 'D:\Program Files\MATLAB\R2021b\extern\engines\python'
    $setupFile = Join-Path $matlabEnginePath 'setup.py'

    # Backup original setup.py
    Write-Host "Backing up setup.py..." -ForegroundColor Yellow
    Copy-Item $setupFile "$setupFile.bak"

    # Fix version number in setup.py (R2021b -> 9.11.0 to comply with PEP 440)
    Write-Host "Patching setup.py version format..." -ForegroundColor Yellow
    $content = Get-Content $setupFile -Raw
    $content = $content -replace 'version="R2021b"', 'version="9.11.0"'
    $content = $content -replace 'from distutils.core import setup', 'from setuptools import setup'
    Set-Content $setupFile $content -NoNewline

    # Install MATLAB Engine
    Set-Location $matlabEnginePath
    & $pythonPath setup.py install

    # Restore original setup.py
    Write-Host "Restoring original setup.py..." -ForegroundColor Yellow
    Move-Item "$setupFile.bak" $setupFile -Force

    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n=== Step 3: Verify installation ===" -ForegroundColor Green
        & $pythonPath -c "import matlab.engine; import sys; sys.stdout.buffer.write(b'MATLAB Engine Installed Successfully!')"

        Write-Host "`n=== Setup Complete! ===" -ForegroundColor Green
    } else {
        Write-Host "`n=== MATLAB Engine installation failed ===" -ForegroundColor Red
    }
} else {
    Write-Host "`n=== Failed to install dependencies ===" -ForegroundColor Red
}
