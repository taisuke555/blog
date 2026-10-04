@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where python >nul 2>nul
if errorlevel 1 (
  echo Python が見つかりません。https://www.python.org/ からインストールしてください。
  pause & exit /b 1
)
python -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo [準備] Flask をインストールしています...
  python -m pip install flask -q
)
where claude >nul 2>nul
if errorlevel 1 (
  echo [注意] Claude Code が見つかりません。インストール後に再起動してください。
  echo         それまでは settings.json の engine を mock にすると画面だけ試せます。
)
echo 台本一貫スタジオを起動します: http://localhost:5071
start "" http://localhost:5071
python app.py
pause
