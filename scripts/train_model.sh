#!/bin/bash
# Phase 2: 訓練 ML 模型
# 在收集至少 6 小時數據後執行此腳本

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "====================================="
echo "  PolyQuant-5m 模型訓練"
echo "====================================="

# 激活虛擬環境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 找不到虛擬環境，請先運行 setup_gcp.sh"
    exit 1
fi

# 檢查訓練數據
TICK_COUNT=$(ls data/raw/ticks_*.csv 2>/dev/null | wc -l)
if [ "$TICK_COUNT" -eq 0 ]; then
    echo "❌ 找不到 Tick 數據 (data/raw/ticks_*.csv)"
    echo "   請先運行機器人至少 6 小時收集數據"
    exit 1
fi

echo "✅ 找到 $TICK_COUNT 個 Tick 數據文件"

# 選擇訓練模式
echo ""
echo "選擇訓練 Label 來源:"
echo "  [1] Binance 現貨價格 (快速，適合初期)"
echo "  [2] Chainlink Oracle 價格 (精確，Phase 3，需時較長)"
read -p "選擇 [1/2]: " LABEL_MODE

if [ "$LABEL_MODE" = "2" ]; then
    echo "🔗 使用 Chainlink Oracle Label (Phase 3)..."
    python -c "
import asyncio
from src.data_engine.label_generator import OracleLabelGenerator
async def run():
    gen = OracleLabelGenerator()
    df = await gen.generate()
    print(f'Oracle Label 生成完成: {len(df)} 條數據')
asyncio.run(run())
"
    # 讓模型訓練器使用 Oracle Label
    export USE_ORACLE_LABEL=true
fi

echo "🤖 開始訓練 LightGBM 模型..."
python -c "
from src.strategy_engine.model_trainer import ModelTrainer
trainer = ModelTrainer()
metrics = trainer.train()
print(f'\n✅ 訓練完成!')
print(f'   LogLoss: {metrics[\"log_loss\"]:.4f}')
print(f'   Brier Score: {metrics[\"brier_score\"]:.4f}')
print(f'   驗證集大小: {metrics[\"n_val\"]:,} 條')
"

echo ""
echo "✅ 模型已保存到 models/calibrated_lgb.pkl"
echo "   重啟機器人後將自動加載 ML 模型 (Phase 2 激活)"
