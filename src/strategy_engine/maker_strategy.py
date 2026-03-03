"""
Maker 做市策略 - Phase 4
在 Polymarket CLOB 上掛雙邊限價單，賺取 Spread

策略邏輯:
  1. 計算公允價值 (Fair Value) = ML 模型概率
  2. 在公允價值兩側掛 Bid/Ask
  3. 根據庫存偏斜動態調整報價
  4. 訂單超時或成交後重新掛單
"""
import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from src.strategy_engine.model_inference import ModelInference
from src.data_engine.feature_calculator import FeatureCalculator
from src.data_engine.polymarket_stream import PolymarketStream
from src.execution_engine.inventory_manager import InventoryManager
from src.utils.logger import trade_logger as logger


@dataclass
class MakerQuote:
    """做市商報價"""
    bid_price: float   # 買入報價 (賣 NO)
    ask_price: float   # 賣出報價 (賣 YES)
    bid_size: float    # 買入數量
    ask_size: float    # 賣出數量
    fair_value: float  # 公允價值
    skew: float        # 庫存偏斜


class MakerStrategy:
    """
    Polymarket CLOB 做市策略

    核心參數:
    - spread: 最小點差 (e.g. 0.02 = 2%)
    - max_position: 最大持倉 (USD)
    - quote_size: 每次掛單金額
    - refresh_interval: 重新掛單間隔 (秒)
    """

    def __init__(
        self,
        model_inference: ModelInference,
        feature_calc: FeatureCalculator,
        polymarket: PolymarketStream,
        inventory_mgr: InventoryManager,
        config: Dict
    ):
        self.model = model_inference
        self.feature_calc = feature_calc
        self.polymarket = polymarket
        self.inventory = inventory_mgr
        self.cfg = config.get('maker', {
            'spread': 0.02,
            'max_position_usd': 200.0,
            'quote_size_usd': 20.0,
            'refresh_interval_s': 10,
            'min_edge': 0.005,
            'skew_factor': 0.5
        })

        self.active_orders: Dict[str, dict] = {}  # order_id -> order_info
        self.is_running = False

    # ------------------------------------------------------------------
    # 主循環
    # ------------------------------------------------------------------

    async def run(self, order_executor):
        """
        做市主循環

        Args:
            order_executor: OrderExecutor 實例，用於下單/撤單
        """
        self.is_running = True
        logger.info("🏪 Maker 做市策略已啟動")

        while self.is_running:
            try:
                await self._refresh_quotes(order_executor)
                await asyncio.sleep(self.cfg['refresh_interval_s'])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Maker 循環異常: {e}")
                await asyncio.sleep(5)

        # 停止時撤銷所有掛單
        await self._cancel_all_orders(order_executor)
        logger.info("🏪 Maker 策略已停止，已撤銷所有掛單")

    async def stop(self):
        self.is_running = False

    # ------------------------------------------------------------------
    # 報價計算
    # ------------------------------------------------------------------

    def compute_quote(self) -> Optional[MakerQuote]:
        """
        計算當前最優做市報價

        Returns:
            MakerQuote 或 None (市場狀態不適合做市)
        """
        if self.polymarket.is_stale:
            return None

        # 獲取公允價值 (ML 模型概率)
        features = self.feature_calc.compute_features()
        fair_value, valid = self.model.predict_proba(features)

        if not valid:
            # 模型未加載時，用 Polymarket 中間價作為公允值
            pm = self.polymarket.get_current_price()
            bid = pm.get('best_bid', 0)
            ask = pm.get('best_ask', 1)
            if bid <= 0 or ask <= 0:
                return None
            fair_value = (bid + ask) / 2

        # 庫存偏斜調整
        skew = self.inventory.compute_skew(
            max_position=self.cfg['max_position_usd'],
            skew_factor=self.cfg['skew_factor']
        )

        # 調整後的公允價值
        adjusted_fv = fair_value + skew
        adjusted_fv = max(0.01, min(0.99, adjusted_fv))

        half_spread = self.cfg['spread'] / 2
        quote_size = self.cfg['quote_size_usd']

        return MakerQuote(
            bid_price=round(adjusted_fv - half_spread, 4),
            ask_price=round(adjusted_fv + half_spread, 4),
            bid_size=quote_size,
            ask_size=quote_size,
            fair_value=fair_value,
            skew=skew
        )

    def should_quote(self, quote: MakerQuote) -> bool:
        """
        判斷是否應該掛單
        條件: 報價在合理範圍內 + 庫存未超限
        """
        if quote.bid_price <= 0.01 or quote.ask_price >= 0.99:
            return False

        if quote.bid_price >= quote.ask_price:
            return False

        if self.inventory.is_position_limit_reached(self.cfg['max_position_usd']):
            logger.warning("庫存已達上限，暫停做市")
            return False

        return True

    # ------------------------------------------------------------------
    # 訂單管理
    # ------------------------------------------------------------------

    async def _refresh_quotes(self, order_executor):
        """撤銷舊報價，掛新報價"""
        quote = self.compute_quote()
        if quote is None:
            return

        if not self.should_quote(quote):
            return

        # 撤銷舊掛單
        if self.active_orders:
            await self._cancel_all_orders(order_executor)

        # 掛新報價
        logger.info(
            f"📋 做市報價 | FV={quote.fair_value:.4f} "
            f"Bid={quote.bid_price:.4f} Ask={quote.ask_price:.4f} "
            f"Skew={quote.skew:+.4f}"
        )

        # 掛 Bid (買入 YES)
        bid_order = await order_executor.place_limit_order(
            side="BUY",
            price=quote.bid_price,
            size=quote.bid_size,
            token_side="YES"
        )
        if bid_order:
            self.active_orders[bid_order['order_id']] = {
                **bid_order, 'side': 'bid', 'quote': quote
            }

        # 掛 Ask (賣出 YES / 買入 NO)
        ask_order = await order_executor.place_limit_order(
            side="SELL",
            price=quote.ask_price,
            size=quote.ask_size,
            token_side="YES"
        )
        if ask_order:
            self.active_orders[ask_order['order_id']] = {
                **ask_order, 'side': 'ask', 'quote': quote
            }

    async def _cancel_all_orders(self, order_executor):
        """撤銷所有活躍掛單"""
        for order_id in list(self.active_orders.keys()):
            try:
                await order_executor.cancel_order(order_id)
                logger.debug(f"已撤銷掛單: {order_id}")
            except Exception as e:
                logger.warning(f"撤單失敗 {order_id}: {e}")
        self.active_orders.clear()
