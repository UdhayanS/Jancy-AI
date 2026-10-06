# Builds dist\Jancy\Jancy.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -q -r requirements.txt pyinstaller
python tools\make_icon.py
# Generate the comtypes UI Automation wrapper so it gets bundled.
python -c "import uiautomation"

python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name Jancy `
    --icon assets\jancy.ico `
    --hidden-import pynput.keyboard._win32 `
    --hidden-import pynput.mouse._win32 `
    --collect-submodules comtypes.gen `
    --exclude-module tkinter `
    run_jancy.pyw

Write-Host "`nBuilt: $PSScriptRoot\dist\Jancy\Jancy.exe" -ForegroundColor Green
