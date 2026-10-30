@echo off
rem dist\yes-quiet.exe 한 파일로 묶는다. 아이콘을 바꿨다면 먼저 python make_icon.py
python -m PyInstaller --noconfirm --onefile --windowed --name yes-quiet --icon icon.ico --add-data "icon.png;." --add-data "icon-32.png;." --add-data "icon-day.png;." --add-data "icon-night.png;." yes-quiet.pyw
