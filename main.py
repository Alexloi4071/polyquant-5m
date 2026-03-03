"""
PolyQuant-5m 主程序入口

架構: 三個異步協程並發運行
  1. binance.connect()     - Binance Tick 數據流
  2. polymarket.connect()  - Polymarket CLOB 訂單簿
  3. strategy_loop()       - 策略評估與訂單執行 (100ms 心跳)

啟動方式:
  export POLYMARKET_TOKEN_ID="your_token_id"
  python main.py
"""
import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import setup_logger, logger
from src.data_engine.binance_stream import BinanceStream
from src.data_engine.polymarket_stream import PolymarketStream
from src.strategy_engine.alpha_calculator import AlphaCalculator
from src.strategy_engine.signal_generator import SignalGenerator
from src.execution_engine.order_executor import OrderExecutor
from src.execution_engine.risk_manager import RiskManager


class PolyQuantBot:
    """PolyQuant-5m 交易機器人主控制器"""

    def __init__(self, config_path: str = "config/config.yaml"):
        load_dotenv()

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        setup_logger(os.getenv("LOG_LEVEL", "INFO"))

        # 參數
        self.trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
        self.bankroll = float(os.getenv("POSITION_SIZE_USD", 100)) * 10
        self.max_size = float(os.getenv("POSITION_SIZE_USD", 10))
        self.token_id = os.getenv("POLYMARKET_TOKEN_ID", "")

        if not self.token_id:
            logger.error("❌ 請在 .env 設置 POLYMARKET_TOKEN_ID")
            sys.exit(1)

        # 初始化各模塊
        self.binance = BinanceStream()
        self.polymarket = PolymarketStream(self.token_id)
        self.alpha_calc = AlphaCalculator(
            alpha_threshold=self.config["strategy"]["alpha_threshold"],
            kelly_fraction=self.config["strategy"]["kelly_fraction"]
        )
        self.signal_gen = SignalGenerator(
            self.binance, self.polymarket, self.alpha_calc, self.config
        )
        self.risk_mgr = RiskManager(
            max_daily_loss=self.config["risk"]["max_daily_loss"],
            max_single_loss=self.config["risk"]["max_single_loss"],
            max_consecutive_losses=self.config["risk"]["max_consecutive_losses"]
        )

        # 訂單執行器 (僅實盤模式)
        self.executor: OrderExecutor = None
        if self.trading_enabled:
            pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
            if not pk:
                logger.error("❌ 實盤模式需要 POLYMARKET_PRIVATE_KEY")
                sys.exit(1)
            self.executor = OrderExecutor(pk)
            logger.warning("⚠️  實盤交易模式已啟用！")
        else:
            logger.info("📊 Paper Trading 模式（不會真實下單）")

    async def strategy_loop(self):
        """策略執行循環 - 100ms 心跳"""
        while True:
            try:
                await asyncio.sleep(0.1)

                # 等待數據就緒
                if self.binance.last_price == 0:
                    continue

                # 評估信號
                signal = self.signal_gen.evaluate(bankroll=self.bankroll)
                if signal is None:
                    continue

                # 風控檢查
                risk_check = self.risk_mgr.check_trade_allowed(signal["size"])
                if not risk_check["allowed"]:
                    logger.warning(f"🛑 風控攔截: {risk_check['reason']}")
                    continue

                # 倉位限制
                trade_size = min(signal["size"], self.max_size)

                if self.trading_enabled and self.executor:
                    pm = self.polymarket.get_current_price()
                    result = self.executor.execute_fok(
                        token_id=self.token_id,
                        price=pm["best_ask"],
                        size=trade_size,
                        side="BUY"
                    )
                    if result["success"]:
                        self.risk_mgr.record_trade_result(-trade_size * 0.02)  # 保守估計
                else:
                    pm = self.polymarket.get_current_price()
                    logger.info(
                        f"📝 [PAPER] BUY ${trade_size:.2f} @ {pm['best_ask']:.4f} | "
                        f"EV={signal['ev']:.4f} | Alpha={signal['alpha']:.4f} | "
                        f"策略={signal.get('strategy', 'N/A')}"
                    )

            except Exception as e:
                logger.error(f"策略循環異常: {e}")

    async def run(self):
        """啟動所有協程"""
        logger.info("=" * 60)
        logger.info("  PolyQuant-5m 啟動中...")
        logger.info(f"  Token ID : {self.token_id}")
        logger.info(f"  模式     : {'🔴 LIVE' if self.trading_enabled else '🟢 PAPER'}")
        logger.info(f"  最大倉位 : ${self.max_size} USDC")
        logger.info("=" * 60)

        await asyncio.gather(
            self.binance.connect(),
            self.polymarket.connect(),
            self.strategy_loop()
        )


if __name__ == "__main__":
    try:
        bot = PolyQuantBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("\n用戶中斷，安全退出")
    except Exception as e:
        logger.critical(f"致命錯誤: {e}")
        sys.exit(1)
