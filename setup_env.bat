@echo off
setlocal enabledelayedexpansion

echo ============================================
echo Node Walker - Environment Setup
echo ============================================
echo.

echo Checking for Python 3.11...
:: Use the py launcher to strictly check for and enforce 3.11
py -3.11 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python 3.11 was not found on your system.
    echo.
    echo Please install Python 3.11 from https://www.python.org/
    echo Ensure you install the "Python Launcher for Windows" during setup.
    pause
    exit /b 1
)

echo Python 3.11 detected successfully.
echo.

echo Creating virtual environment (.venv) strictly with Python 3.11...
if exist .venv (
    echo Removing existing virtual environment...
    rmdir /s /q .venv >nul 2>&1
)

:: Enforce 3.11 for the venv creation
py -3.11 -m venv .venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)

echo.
echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo.
echo Upgrading pip...
python -m pip install --upgrade pip setuptools wheel >nul 2>&1

echo.
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo WARNING: Some dependencies failed to install.
    echo This may be OK if ollama library installation failed.
    echo.
)

echo.
echo ============================================
echo Setup Complete!
echo ============================================
echo.
echo NEXT STEPS:
echo.
echo 1. Install and run Ollama:
echo    - Download from: https://ollama.ai
echo    - Run: ollama serve
echo    - In another terminal: ollama pull phi-3-mini
echo.
echo 2. Run Node Walker:
echo    - Activate: .venv\Scripts\activate
echo    - Run: python -m src.app
echo.
echo 3. Access Circuit Highlighting:
echo    - Load a cartridge file
echo    - Click "Circuit Highlighting" tab
echo    - Click Settings (gear icon) to install models
echo.
echo ============================================
pause