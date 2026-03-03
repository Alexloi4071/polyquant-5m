"""
期望值 (EV) 與 Alpha 計算模塊
核心公式: EV = p * (1 - ask) - (1 - p) * ask - fee
使用 Fractional Kelly Criterion 計算倉位
"""
from typing import Dict

from src.utils.logger import trade_logger as logger


class AlphaCalculator:
    """EV 與 Kelly Criterion 計算器"""

    def __init__(self, alpha_threshold: float = 0.05, kelly_fraction: float = 0.25,
                 fee: float = 0.01, config: dict = None):
        """
        Args:
            alpha_threshold: 最低 Alpha 要求 (模型概率 - 市場價格)
            kelly_fraction:  Kelly 縮放係數 (0.25 = 1/4 Kelly，保守策略)
            fee:             FIX: Polymarket 實際 taker fee ~1% (原錯誤值 2%)
            config:          FIX Bug1: 從 config dict 讀取，與 main.py 接口對齊
        """
        # FIX Bug1: 支持從 config dict 讀取參數
        if config:
            strategy_cfg = config.get('strategy', {})
            alpha_threshold = strategy_cfg.get('alpha_threshold', alpha_threshold)
            kelly_fraction = strategy_cfg.get('kelly_fraction', kelly_fraction)
            fee = strategy_cfg.get('fee', fee)

        self.alpha_threshold = alpha_threshold
        self.kelly_fraction = kelly_fraction
        self.fee = fee

    # ------------------------------------------------------------------
    # 核心計算
    # ------------------------------------------------------------------

    def calculate_ev(self, model_prob: float, market_price: float) -> Dict:
        """
        計算期望值 (Expected Value)

        Args:
            model_prob:   模型預測的 YES 勝率 (0~1)
            market_price: Polymarket 當前 Ask 價格 (0~1)

        Returns:
            dict with ev_gross, ev_net, alpha, should_trade
        """
        # 毛 EV: 贏得 (1 - price)，輸掉 price
        ev_gross = model_prob * (1.0 - market_price) - (1.0 - model_prob) * market_price
        ev_net = ev_gross - self.fee
        alpha = model_prob - market_price

        return {
            "ev_gross": round(ev_gross, 6),
            "ev_net": round(ev_net, 6),
            "alpha": round(alpha, 6),
            "should_trade": ev_net > 0 and alpha > self.alpha_threshold
        }

    def kelly_size(self, model_prob: float, market_price: float, bankroll: float) -> float:
        """
        Fractional Kelly Criterion 倉位計算

        Returns:
            建議下注金額 (USD)，最低 0
        """
        if market_price <= 0 or market_price >= 1:
            return 0.0

        # b = 賠率 (net odds)
        b = (1.0 - market_price) / market_price
        # Kelly: f* = (p*b - q) / b
        kelly_raw = (model_prob * b - (1.0 - model_prob)) / b
        kelly_size = max(0.0, kelly_raw) * self.kelly_fraction * bankroll
        return round(kelly_size, 4)

    # ------------------------------------------------------------------
    # 統一信號接口
    # ------------------------------------------------------------------

    def check_signal(self, model_prob: float, ask_price: float,
                     bankroll: float = 1000.0) -> Dict:
        """
        統一信號檢查接口，供 signal_generator.py 調用

        Returns:
            {
                "action": "BUY" | "PASS",
                "size": float,
                "ev": float,
                "alpha": float,
                "reason": str
            }
            注意: price 字段由 signal_generator.py 在返回前補充
        """
        ev = self.calculate_ev(model_prob, ask_price)

        if not ev["should_trade"]:
            return {
                "action": "PASS",
                "size": 0.0,
                "ev": ev["ev_net"],
                "alpha": ev["alpha"],
                "reason": f"Alpha 不足 ({ev['alpha']:.4f} < {self.alpha_threshold})"
            }

        size = self.kelly_size(model_prob, ask_price, bankroll)

        logger.info(
            f"🎯 EV 信號: model={model_prob:.3f} ask={ask_price:.3f} "
            f"alpha={ev['alpha']:.4f} ev_net={ev['ev_net']:.4f} size=${size:.2f}"
        )

        return {
            "action": "BUY",
            "size": size,
            "ev": ev["ev_net"],
            "alpha": ev["alpha"],
            "reason": "Positive EV with sufficient alpha"
        }
