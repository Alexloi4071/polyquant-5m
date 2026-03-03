"""
信號生成器
整合多種策略邏輯，輸出最終交易信號

Phase 1: 延遲套利 (Latency Arbitrage)
Phase 2: OBI/CVD 失衡信號 (待接入 ML 模型後啟用)
"""
from typing import Dict, Optional

from src.data_engine.binance_stream import BinanceStream
from src.data_engine.polymarket_stream import PolymarketStream
from src.strategy_engine.alpha_calculator import AlphaCalculator
from src.utils.logger import trade_logger as logger


class SignalGenerator:
    """多策略信號生成器"""

    def __init__(
        self,
        binance: BinanceStream,
        polymarket: PolymarketStream,
        alpha_calc: AlphaCalculator,
        config: Dict
    ):
        self.binance = binance
        self.polymarket = polymarket
        self.alpha_calc = alpha_calc
        self.cfg = config["strategy"]

    def evaluate(self, bankroll: float = 1000.0) -> Optional[Dict]:
        """
        評估當前市場狀態，輸出交易信號

        Returns:
            signal dict 或 None (無信號)
        """
        # 安全檢查: 報價不能陳舊
        if self.polymarket.is_stale:
            logger.debug("Polymarket 報價陳舊，跳過")
            return None

        # === Strategy A: Latency Arbitrage (Phase 1) ===
        price_change_1s = self.binance.get_1s_price_change()
        threshold = self.cfg["price_change_threshold"]

        if abs(price_change_1s) >= threshold:
            direction = "UP" if price_change_1s > 0 else "DOWN"
            pm = self.polymarket.get_current_price()

            if direction == "UP" and pm["best_ask"] > 0:
                # 上漲套利: 買 YES
                # Phase 2 時替換為真實模型預測值
                estimated_prob = 0.60  # TODO: 替換為 model.predict(features)
                signal = self.alpha_calc.check_signal(estimated_prob, pm["best_ask"], bankroll)

                if signal["action"] == "BUY":
                    signal["strategy"] = "LATENCY_ARB"
                    signal["trigger"] = f"BinanceMove={price_change_1s*100:.3f}%"
                    signal["token_side"] = "YES"
                    return signal

            elif direction == "DOWN" and pm["best_bid"] > 0:
                # 下跌套利: 買 NO (即賣出 YES)
                # TODO: 擴展 NO token 邏輯
                pass

        # === Strategy B: OBI 失衡信號 (Phase 2 啟用) ===
        obi = self.binance.get_obi()
        if abs(obi) >= self.cfg["obi_threshold"]:
            flow = self.binance.get_taker_flow(100)
            logger.debug(f"OBI={obi:.3f} BuyRatio={flow['buy_ratio']:.3f}")
            # TODO: 接入 ML 模型特徵向量

        return None
