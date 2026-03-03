#!/bin/bash
# =====================================================
# PolyQuant-5m 啟動腳本
# =====================================================

# 進入項目目錄
cd "$(dirname "$0")/.."

# 激活虛擬環境
source venv/bin/activate

# 載入環境變數
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "❌ 找不到 .env 文件，請先複製 .env.example 並填入配置"
    exit 1
fi

# 檢查 Token ID
if [ -z "$POLYMARKET_TOKEN_ID" ]; then
    echo "❌ 請在 .env 設置 POLYMARKET_TOKEN_ID"
    exit 1
fi

echo "🚀 啟動 PolyQuant-5m..."
echo "   Trading Mode: $TRADING_ENABLED"
echo "   Token ID: $POLYMARKET_TOKEN_ID"
echo ""

python main.py
