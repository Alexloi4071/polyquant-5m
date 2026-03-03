#!/bin/bash
# Phase 3: 生成 Chainlink Oracle Label
# 用於替換 Binance 現貨 Label，提高模型精確度

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
source venv/bin/activate

echo "🔗 Phase 3: Chainlink Oracle Label 生成"
echo "   合約: BTC/USD Polygon (0xc907E116054Ad103354f2D350FD2514433D57F6f)"
echo ""

python -c "
import asyncio
from src.data_engine.label_generator import OracleLabelGenerator

async def run():
    gen = OracleLabelGenerator(
        data_dir='data/raw',
        output_dir='data/processed',
        forward_window_s=300,  # 5 分鐘
        tolerance_s=30
    )
    df = await gen.generate()
    print(f'\n✅ Oracle Label 生成完成!')
    print(f'   總樣本: {len(df):,}')
    print(f'   UP 比例: {df[\"oracle_label\"].mean():.3f}')
    print(f'   已保存: data/processed/labeled_ticks.csv')
asyncio.run(run())
"
