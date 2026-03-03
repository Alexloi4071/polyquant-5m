# PolyQuant-5m

**Polymarket BTC 5分鐘預測量化交易系統**

微觀套利引擎，基於 Binance 延遲套利 + 訂單流失衡分析

---

## 快速開始

### 1. GCP VM 配置

- 區域: `europe-west2-a` (倫敦) ← **必須！靠近 Polymarket 伺服器**
- 機型: `e2-medium` (2 vCPU, 4GB RAM)
- 系統: Ubuntu 22.04 LTS

### 2. 部署

```bash
# SSH 到 VM 後
git clone https://github.com/Alexloi4071/polyquant-5m.git
cd polyquant-5m
chmod +x scripts/*.sh
./scripts/setup_gcp.sh
```

### 3. 配置

```bash
cp .env.example .env
nano .env   # 填入 POLYMARKET_PRIVATE_KEY 和 POLYMARKET_TOKEN_ID
```

### 4. 測試連線

```bash
source venv/bin/activate
python tests/test_connections.py
```

### 5. 啟動 (Paper Trading)

```bash
./scripts/start_bot.sh
```

---

## 項目結構

```
src/
├── data_engine/
│   ├── binance_stream.py       # Binance Tick 流 + OBI/CVD 計算
│   └── polymarket_stream.py    # Polymarket CLOB 訂單簿
├── strategy_engine/
│   ├── alpha_calculator.py     # EV + Kelly Criterion
│   └── signal_generator.py     # 多策略信號生成
├── execution_engine/
│   ├── order_executor.py       # FOK 訂單執行
│   └── risk_manager.py         # 風控熔斷
└── utils/
    └── logger.py               # 結構化日誌
```

## 數據流向

```
Binance WS ──► binance_stream (OBI/CVD)
                       │
Polymarket WS ──► polymarket_stream (Ask price)
                       │
               signal_generator
                       │
               alpha_calculator (EV / Kelly)
                       │
               risk_manager (熔斷檢查)
                       │
               order_executor (FOK 下單)
```

## 開發路線圖

- [x] **Phase 1**: 延遲套利基礎架構
- [ ] **Phase 2**: 接入 LightGBM 概率模型 (Isotonic Calibration)
- [ ] **Phase 3**: Chainlink Oracle Label 對齊
- [ ] **Phase 4**: Maker 做市策略

## 風險提示

- 請先在 Paper Trading 模式充分測試
- 確保 Polygon 錢包有足夠 USDC 和 MATIC (gas)
- 每日虧損達上限時系統會自動熔斷

---

> ⚠️ 本項目僅供學習研究，不構成投資建議
