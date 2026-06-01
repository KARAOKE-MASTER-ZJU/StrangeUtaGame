@echo off
echo ==========================================
echo  Setting up StrangeUtaGame Development Environment
echo ==========================================
echo.

REM Check if conda is available
conda --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Conda not found. Please install Anaconda or Miniconda first.
    exit /b 1
)

echo Step 1: Creating conda environment 'sug' with Python 3.11...
conda create -n sug python=3.11 -y
if errorlevel 1 (
    echo ERROR: Failed to create conda environment
    exit /b 1
)

echo.
echo Step 2: Activating environment...
call conda activate sug

echo.
echo Step 3: Installing project with win+dev extras...
:: Windows: 安装 winrt-* (WinRT IME 注音主引擎) + 开发工具
pip install -e ".[win,dev]"

echo.
echo Step 4: Installing project in editable mode (已完成上一步)...

echo.
echo ==========================================
echo  Setup Complete!
echo ==========================================
echo.
echo To activate the environment, run:
echo   conda activate sug
echo.
echo To run tests:
echo   python -m pytest tests/unit/domain -v
echo.
pause
