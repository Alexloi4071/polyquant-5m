#!/bin/bash
# =====================================================
# PolyQuant-5m GCP VM 初始化腳本
# 在全新的 Ubuntu 22.04 VM 上執行
# =====================================================

set -e  # 遇到錯誤立即停止

echo "================================"
echo "PolyQuant-5m GCP 環境初始化"
echo "================================"

# 更新系統
sudo apt-get update -y && sudo apt-get upgrade -y

# 安裝必要系統依賴
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    curl \
    wget \
    tmux \
    build-essential \
    libssl-dev \
    unzip

# 創建虛擬環境
python3.11 -m venv venv
source venv/bin/activate

# 升級 pip
pip install --upgrade pip setuptools wheel

# 安裝項目依賴
pip install -r requirements.txt

# 創建必要目錄
mkdir -p logs data/raw data/processed data/backtest models

echo ""
echo "✅ 環境初始化完成!"
echo ""
echo "下一步:"
echo "  1. cp .env.example .env"
echo "  2. nano .env  (填入你的密鑰)"
echo "  3. ./scripts/start_bot.sh"
echo ""
