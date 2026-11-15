@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
:loop
.venv\Scripts\python bot.py
echo Bot stopped, restarting in 30 seconds...
timeout /t 30 /nobreak >nul
goto loop
