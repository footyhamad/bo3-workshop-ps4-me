$ErrorActionPreference = "Stop"
python -m pip install --upgrade pip
python -m pip install -e ".[build,dev]"
python -m compileall -q src tests scripts
python -m pytest -q
python -m PyInstaller --noconfirm --clean --onefile --windowed --name BO3WorkshopDownloader --paths src scripts\launcher.py
Write-Host "Built: dist\BO3WorkshopDownloader.exe"
