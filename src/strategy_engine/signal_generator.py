"""
信號生成器 - Phase 1 + 2 集成版

Phase 1: 延遲套利 (Latency Arbitrage) - 純規則
Phase 2: OBI/CVD + ML 模型預測概率
"""
from typing import Dict, Optional

from src.data_engine.binance_stream import BinanceStream
from src.data_engine.polymarket_stream import PolymarketStream
from src.data_engine.feature_calculator import FeatureCalculator
from src.strategy_engine.alpha_calculator import AlphaCalculator
from src.strategy_engine.model_inference import ModelInference
from src.utils.logger import trade_logger as logger


class SignalGenerator:
    """多策略信號生成器 (Phase 1 + Phase 2)"""

    def __init__(
        self,
        binance: BinanceStream,
        polymarket: PolymarketStream,
        alpha_calc: AlphaCalculator,
        feature_calc: FeatureCalculator,
        model_inference: ModelInference,
        config: Dict
    ):
        self.binance = binance
        self.polymarket = polymarket
        self.alpha_calc = alpha_calc
        self.feature_calc = feature_calc
        self.model = model_inference
        self.cfg = config["strategy"]

    def evaluate(self, bankroll: float = 1000.0) -> Optional[Dict]:
        """
        評估當前市場狀態，輸出交易信號
        策略優先級: Phase 2 ML > Phase 1 Latency Arb
        """
        if self.polymarket.is_stale:
            logger.debug("Polymarket 報價陳舊，跳過")
            return None

        pm = self.polymarket.get_current_price()
        
        # 核心：檢查基準價 (Price to Beat) 是否有效
        # 如果 Polymarket 和 Binance 都沒給出基準價，跳過
        if pm.get('price_to_beat', 0) <= 0:
            logger.debug("基準價 (Price to Beat) 無效，跳過")
            return None

        # FIX: 任何策略入場前先檢查 PM 點差
        # Polymarket BTC 5m 常見點差 3-7%，點差過寬時
        # taker EV 必然為負，還沒比前已先輸給市場
        pm_spread = pm.get('best_ask', 1.0) - pm.get('best_bid', 0.0)
        max_spread = self.cfg.get('max_pm_spread', 0.07)
        if pm_spread > max_spread or pm.get('best_bid', 0) <= 0 or pm.get('best_ask', 0) <= 0:
            logger.debug(f"PM 點差過寬 ({pm_spread:.3f} > {max_spread:.3f})，跳過")
            return None

        features = self.feature_calc.compute_features()

        # === Strategy B: ML 模型信號 (Phase 2) ===
        if self.model.is_loaded:
            signal = self._evaluate_ml_signal(features, pm, bankroll)
            if signal:
                return signal

        # === Strategy A: Latency Arbitrage (Phase 1 Fallback) ===
        return self._evaluate_latency_arb(features, pm, bankroll)

    # ------------------------------------------------------------------
    # Phase 2: ML 模型信號
    # ------------------------------------------------------------------

    def _evaluate_ml_signal(self, features: dict, pm: dict, bankroll: float) -> Optional[Dict]:
        """ML 模型信號評估"""
        obi_threshold = self.cfg.get("obi_threshold", 0.15)
        obi_now = features.get('obi_30s', 0.0)

        if abs(obi_now) < obi_threshold:
            return None

        direction = "UP" if obi_now > 0 else "DOWN"

        if direction == "UP" and pm.get("best_ask", 0) > 0:
            prob_up, valid = self.model.predict_proba(features)
            if not valid:
                return None

            ask_price = pm["best_ask"]
            signal = self.alpha_calc.check_signal(prob_up, ask_price, bankroll)
            if signal["action"] == "BUY":
                signal["strategy"] = "ML_OBI"
                signal["price"] = ask_price
                signal["trigger"] = (
                    f"OBI30s={obi_now:.3f} "
                    f"CVD={features.get('cvd_30s', 0):.2f} "
                    f"ModelProb={prob_up:.3f}"
                )
                signal["token_side"] = "YES"
                signal["features"] = features
                logger.info(f"📊 ML 信號: {signal['trigger']}")
                return signal

        elif direction == "DOWN" and pm.get("best_bid", 0) > 0:
            # FIX: 直接用 P(DOWN) = 1 - P(UP)，不再反轉特徵
            prob_up, valid = self.model.predict_proba(features)
            if not valid:
                return None

            prob_no = 1.0 - prob_up
            bid_price = pm["best_bid"]
            signal = self.alpha_calc.check_signal(prob_no, bid_price, bankroll)
            if signal["action"] == "BUY":
                signal["strategy"] = "ML_OBI_SHORT"
                signal["price"] = bid_price
                signal["trigger"] = (
                    f"OBI30s={obi_now:.3f} "
                    f"CVD={features.get('cvd_30s', 0):.2f} "
                    f"ModelProb(NO)={prob_no:.3f}"
                )
                signal["token_side"] = "NO"
                signal["features"] = features
                logger.info(f"📊 ML 空頭信號: {signal['trigger']}")
                return signal

        return None

    # ------------------------------------------------------------------
    # Phase 1: Latency Arbitrage Fallback
    # ------------------------------------------------------------------

    def _evaluate_latency_arb(self, features: dict, pm: dict, bankroll: float) -> Optional[Dict]:
        """延遲套利信號評估"""
        price_change_1s = self.binance.get_1s_price_change()
        threshold = self.cfg["price_change_threshold"]

        if abs(price_change_1s) < threshold:
            return None

        direction = "UP" if price_change_1s > 0 else "DOWN"

        if direction == "UP" and pm.get("best_ask", 0) > 0:
            prob, valid = self.model.predict_proba(features)
            estimated_prob = prob if valid else 0.60

            ask_price = pm["best_ask"]
            signal = self.alpha_calc.check_signal(estimated_prob, ask_price, bankroll)
            if signal["action"] == "BUY":
                signal["strategy"] = "LATENCY_ARB"
                signal["price"] = ask_price
                signal["trigger"] = f"BinanceMove={price_change_1s*100:.3f}%"
                signal["token_side"] = "YES"
                return signal

        elif direction == "DOWN" and pm.get("best_bid", 0) > 0:
            prob, valid = self.model.predict_proba(features)
            prob_no = (1.0 - prob) if valid else 0.60

            bid_price = pm["best_bid"]
            signal = self.alpha_calc.check_signal(prob_no, bid_price, bankroll)
            if signal["action"] == "BUY":
                signal["strategy"] = "LATENCY_ARB_SHORT"
                signal["price"] = bid_price
                signal["trigger"] = f"BinanceMove={price_change_1s*100:.3f}%"
                signal["token_side"] = "NO"
                return signal

        return None
