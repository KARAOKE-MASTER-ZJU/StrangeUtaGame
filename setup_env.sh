#!/usr/bin/env bash
# StrangeUtaGame — Linux/macOS 开发环境一键配置
# 使用方法: bash setup_env.sh
set -euo pipefail

echo "=========================================="
echo "  Setting up StrangeUtaGame Development Environment"
echo "=========================================="
echo ""

# 检查 conda
if ! command -v conda &> /dev/null; then
    echo "ERROR: Conda not found. Please install Anaconda or Miniconda first."
    exit 1
fi

echo "Step 1: Creating conda environment 'sug' with Python 3.11..."
conda create -n sug python=3.11 -y

echo ""
echo "Step 2: Activating environment..."
# conda activate 在脚本中需要 eval
eval "$(conda shell.bash hook)"
conda activate sug

echo ""
echo "Step 3: Installing project with unix+dev extras..."
# Unix 系统自动安装 fugashi + unidic-lite 作为注音回退引擎
# Windows 用户请使用 setup_env.bat（安装 winrt 作为主引擎）
pip install -e ".[unix,dev]"

echo ""
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "To activate the environment, run:"
echo "  conda activate sug"
echo ""
echo "To run tests:"
echo "  python -m pytest tests/unit/domain -v"
echo ""
