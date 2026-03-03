# PolyQuant-5m

> Polymarket BTC 5-Minute 預測市場量化交易系統

## 🏗️ 架構總覽

```
PolyQuant-5m/
├── src/
│   ├── data_engine/
│   │   ├── binance_stream.py       # Binance WS 數據流
│   │   ├── polymarket_stream.py    # Polymarket CLOB WS
│   │   ├── feature_calculator.py  # 實時特徵計算 (Phase 2)
│   │   ├── data_collector.py      # Tick 數據落地 (Phase 2)
│   │   ├── chainlink_oracle.py    # Chainlink Oracle 查詢 (Phase 3)
│   │   └── label_generator.py     # Oracle Label 生成 (Phase 3)
│   ├── strategy_engine/
│   │   ├── signal_generator.py    # 信號生成 (Phase 1+2)
│   │   ├── model_trainer.py       # LightGBM 訓練 (Phase 2)
│   │   ├── model_inference.py     # 實時推理 (Phase 2)
│   │   ├── maker_strategy.py      # 做市策略 (Phase 4)
│   │   └── alpha_calculator.py    # EV + Kelly 計算
│   ├── execution_engine/
│   │   ├── order_executor.py      # FOK + Limit 下單 (Phase 1+4)
│   │   ├── inventory_manager.py   # 庫存管理 (Phase 4)
│   │   └── risk_manager.py        # 風控熔斷
│   └── utils/
├── scripts/
│   ├── setup_gcp.sh               # GCP 初始化
│   ├── start_bot.sh               # 啟動機器人
│   ├── train_model.sh             # 訓練模型 (Phase 2)
│   └── generate_oracle_labels.sh  # 生成 Oracle Label (Phase 3)
├── config/config.yaml
├── data/
│   ├── raw/                       # 原始 Tick CSV
│   └── processed/                 # Oracle Label CSV
├── models/                        # 訓練好的模型
└── main.py
```

## 🚀 四個 Phase

| Phase | 策略 | 狀態 | 說明 |
|-------|------|------|------|
| Phase 1 | Latency Arbitrage | ✅ 完整 | Binance 1s 價格衝擊 -> 套利 |
| Phase 2 | ML 模型信號 | ✅ 完整 | LightGBM OBI/CVD 特徵推理 |
| Phase 3 | Oracle Label 對齊 | ✅ 完整 | Chainlink 結算價替換 Binance Label |
| Phase 4 | Maker 做市 | ✅ 完整 | CLOB 雙邊限價掛單 + 庫存管理 |

## ⚡ 快速啟動

```bash
# 1. 克隆並初始化
git clone https://github.com/Alexloi4071/polyquant-5m.git
cd polyquant-5m
./scripts/setup_gcp.sh

# 2. 配置密鑰
cp .env.example .env
nano .env

# 3. 啟動機器人 (Paper Trading)
tmux new -s polyquant
./scripts/start_bot.sh

# 4. 收集 6 小時數據後訓練模型 (Phase 2 激活)
./scripts/train_model.sh

# 5. 使用 Oracle Label 重新訓練 (Phase 3 精化)
./scripts/generate_oracle_labels.sh
# 然後重新運行 train_model.sh

# 6. 啟用 Maker 做市 (Phase 4)
echo 'ENABLE_MAKER=true' >> .env
./scripts/start_bot.sh
```

## 🖥️ 推薦 GCP 配置

- **區域**: `europe-west2` (倫敦，延遲最低)
- **機型**: `e2-small` (2 vCPU, 2GB RAM)
- **系統**: Ubuntu 22.04 LTS
- **靜態 IP**: 必須 (Polymarket 需要穩定出口 IP)
