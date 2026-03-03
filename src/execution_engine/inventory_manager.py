"""
庫存管理器 - Phase 4
追蹤持倉、計算 P&L、生成庫存偏斜信號
"""
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque

from src.utils.logger import trade_logger as logger
from src.utils.helpers import timestamp_ms


@dataclass
class Position:
    """單筆持倉"""
    token_side: str     # YES / NO
    size: float         # 份數
    avg_cost: float     # 平均成本
    timestamp: int      # 建倉時間 (ms)
    strategy: str       # 來源策略


@dataclass
class TradeRecord:
    """成交記錄"""
    order_id: str
    strategy: str
    token_side: str
    side: str           # BUY / SELL
    size: float
    price: float
    pnl: float          # 已實現 P&L (USD)
    timestamp: int


class InventoryManager:
    """
    庫存管理器

    功能:
    - 追蹤 YES/NO token 持倉
    - 計算未實現 + 已實現 P&L
    - 生成庫存偏斜 (用於 Maker 策略報價調整)
    - 觸發倉位平衡警告
    """

    def __init__(self, max_position_usd: float = 500.0):
        self.max_position_usd = max_position_usd

        # 持倉: {token_side: Position}
        self.positions: Dict[str, Optional[Position]] = {
            'YES': None,
            'NO': None
        }

        # 歷史記錄
        self.trade_history: deque = deque(maxlen=1000)
        self.realized_pnl: float = 0.0
        self.total_trades: int = 0

    # ------------------------------------------------------------------
    # 持倉更新
    # ------------------------------------------------------------------

    def on_fill(self, order_id: str, strategy: str, token_side: str,
                side: str, size: float, price: float):
        """
        訂單成交回調

        Args:
            order_id: 訂單 ID
            strategy: 策略名稱
            token_side: YES / NO
            side: BUY / SELL
            size: 成交份數
            price: 成交價格 (0~1)
        """
        trade_pnl = 0.0

        if side == "BUY":
            self._open_position(token_side, size, price, strategy)
        elif side == "SELL":
            trade_pnl = self._close_position(token_side, size, price)

        self.realized_pnl += trade_pnl
        self.total_trades += 1

        record = TradeRecord(
            order_id=order_id,
            strategy=strategy,
            token_side=token_side,
            side=side,
            size=size,
            price=price,
            pnl=trade_pnl,
            timestamp=timestamp_ms()
        )
        self.trade_history.append(record)

        logger.info(
            f"📦 庫存更新 | {side} {size:.2f} {token_side} @ {price:.4f} "
            f"| 已實現P&L: ${self.realized_pnl:.4f}"
        )

    def _open_position(self, token_side: str, size: float, price: float, strategy: str):
        """開倉或加倉"""
        existing = self.positions[token_side]
        if existing is None:
            self.positions[token_side] = Position(
                token_side=token_side,
                size=size,
                avg_cost=price,
                timestamp=timestamp_ms(),
                strategy=strategy
            )
        else:
            # 加倉: 計算新的平均成本
            total_size = existing.size + size
            total_cost = existing.size * existing.avg_cost + size * price
            existing.size = total_size
            existing.avg_cost = total_cost / total_size

    def _close_position(self, token_side: str, size: float, price: float) -> float:
        """平倉，返回已實現 P&L"""
        existing = self.positions[token_side]
        if existing is None:
            logger.warning(f"試圖平倉但無持倉: {token_side}")
            return 0.0

        close_size = min(size, existing.size)
        pnl = close_size * (price - existing.avg_cost)

        if close_size >= existing.size:
            self.positions[token_side] = None
        else:
            existing.size -= close_size

        return pnl

    # ------------------------------------------------------------------
    # 狀態查詢
    # ------------------------------------------------------------------

    def get_net_exposure(self) -> float:
        """計算淨 USD 敞口 (YES - NO)"""
        yes_usd = 0.0
        no_usd = 0.0

        if self.positions['YES']:
            yes_usd = self.positions['YES'].size * self.positions['YES'].avg_cost
        if self.positions['NO']:
            no_usd = self.positions['NO'].size * self.positions['NO'].avg_cost

        return yes_usd - no_usd

    def get_total_position_usd(self) -> float:
        """計算總持倉 USD 價值"""
        total = 0.0
        for side, pos in self.positions.items():
            if pos:
                total += pos.size * pos.avg_cost
        return total

    def compute_skew(self, max_position: float, skew_factor: float = 0.5) -> float:
        """
        計算庫存偏斜，用於 Maker 策略報價調整

        偏斜邏輯:
        - YES 持倉過多 -> 降低 Bid，提高 Ask (不想再買 YES)
        - NO 持倉過多  -> 提高 Bid，降低 Ask (不想再買 NO)

        Returns:
            skew 值 (-0.05 ~ +0.05)
        """
        net_exposure = self.get_net_exposure()
        # 歸一化到 -1 ~ 1
        normalized = net_exposure / (max_position + 1e-9)
        normalized = max(-1.0, min(1.0, normalized))
        # 放大到最大偏斜
        return -normalized * skew_factor * 0.05

    def is_position_limit_reached(self, max_usd: float) -> bool:
        """檢查是否超過持倉上限"""
        return self.get_total_position_usd() >= max_usd

    def get_unrealized_pnl(self, current_prices: Dict[str, float]) -> float:
        """
        計算未實現 P&L

        Args:
            current_prices: {'YES': 0.62, 'NO': 0.38}
        """
        pnl = 0.0
        for side, pos in self.positions.items():
            if pos and side in current_prices:
                pnl += pos.size * (current_prices[side] - pos.avg_cost)
        return pnl

    def summary(self) -> Dict:
        """返回庫存摘要"""
        return {
            'yes_position': self.positions['YES'],
            'no_position': self.positions['NO'],
            'net_exposure_usd': self.get_net_exposure(),
            'total_position_usd': self.get_total_position_usd(),
            'realized_pnl': self.realized_pnl,
            'total_trades': self.total_trades
        }
