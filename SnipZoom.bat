@echo off
cd /d "%~dp0"
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw "%~dp0main.py"
) else (
    start "" py "%~dp0main.py"
)
