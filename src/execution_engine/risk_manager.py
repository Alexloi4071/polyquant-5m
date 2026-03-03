"""
風險管理模塊
實現: 每日虧損熔斷、連續虧損熔斷、倉位上限控制
"""
import time
from typing import Dict

from src.utils.logger import trade_logger as logger


class RiskManager:
    """交易風控守門人"""

    def __init__(self, max_daily_loss: float = 200.0, max_single_loss: float = 50.0,
                 max_consecutive_losses: int = 5):
        self.max_daily_loss = max_daily_loss
        self.max_single_loss = max_single_loss
        self.max_consecutive_losses = max_consecutive_losses

        # 運行狀態
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.is_halted: bool = False
        self._day_start: float = time.time()

    def check_trade_allowed(self, size: float) -> Dict:
        """
        交易前風控檢查

        Returns:
            {"allowed": bool, "reason": str}
        """
        # 重置每日 PnL
        if time.time() - self._day_start > 86400:
            self.daily_pnl = 0.0
            self._day_start = time.time()
            self.is_halted = False
            logger.info("每日 PnL 已重置")

        if self.is_halted:
            return {"allowed": False, "reason": "系統已熔斷，今日停止交易"}

        if self.daily_pnl <= -self.max_daily_loss:
            self.is_halted = True
            return {"allowed": False, "reason": f"已達每日最大虧損上限 ${self.max_daily_loss}"}

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.is_halted = True
            return {"allowed": False, "reason": f"連續虧損 {self.consecutive_losses} 次，熔斷"}

        if size > self.max_single_loss:
            return {"allowed": False, "reason": f"單筆倉位 ${size:.2f} 超過上限 ${self.max_single_loss}"}

        return {"allowed": True, "reason": "OK"}

    def record_trade_result(self, pnl: float):
        """記錄交易結果，更新風控狀態"""
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            logger.warning(
                f"交易虧損: ${pnl:.4f} | 連續虧損: {self.consecutive_losses} 次 | 今日 PnL: ${self.daily_pnl:.4f}"
            )
        else:
            self.consecutive_losses = 0
            logger.info(f"交易盈利: ${pnl:.4f} | 今日 PnL: ${self.daily_pnl:.4f}")
