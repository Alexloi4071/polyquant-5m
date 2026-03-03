"""
PolyQuant-5m 主控程序 - Phase 1 + 2 + 3 + 4 完整版

架構:
  協程 1: Binance WebSocket 數據流
  協程 2: Polymarket CLOB WebSocket 數據流
  協程 3: 信號評估 + 執行循環 (Taker)
  協程 4: Maker 做市循環 (Phase 4)
  協程 5: 數據收集 + 定時落地 (Phase 2)
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.data_engine.binance_stream import BinanceStream
from src.data_engine.polymarket_stream import PolymarketStream
from src.data_engine.feature_calculator import FeatureCalculator
from src.data_engine.data_collector import DataCollector
from src.strategy_engine.alpha_calculator import AlphaCalculator
from src.strategy_engine.signal_generator import SignalGenerator
from src.strategy_engine.model_inference import ModelInference
from src.strategy_engine.maker_strategy import MakerStrategy
from src.execution_engine.order_executor import OrderExecutor
from src.execution_engine.inventory_manager import InventoryManager
from src.execution_engine.risk_manager import RiskManager
from src.utils.logger import logger
from src.utils.config import load_config


def load_environment():
    """加載環境變量"""
    env_file = Path(".env")
    if env_file.exists():
        load_dotenv(env_file)
        logger.info("✅ 環境變量已加載")
    else:
        logger.warning("⚠️  找不到 .env 文件，使用系統環境變量")


def check_required_env():
    """檢查必要環境變量"""
    required = ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_TOKEN_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error(f"缺少必要環境變量: {missing}")
        sys.exit(1)


async def main():
    load_environment()
    check_required_env()

    config = load_config()

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    token_id = os.getenv("POLYMARKET_TOKEN_ID")
    trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
    enable_maker = os.getenv("ENABLE_MAKER", "false").lower() == "true"

    logger.info(f"🚀 PolyQuant-5m 啟動")
    logger.info(f"   交易模式: {'LIVE 🔴' if trading_enabled else 'PAPER 🟡'}")
    logger.info(f"   Maker 策略: {'啟用 ✅' if enable_maker else '禁用 ⏸'}")

    # ----------------------------------------------------------------
    # 初始化所有組件
    # ----------------------------------------------------------------

    # 數據層
    binance = BinanceStream(symbol="btcusdt")
    polymarket = PolymarketStream(token_id=token_id)
    feature_calc = FeatureCalculator(binance_stream=binance)
    data_collector = DataCollector(
        data_dir=config.get('data', {}).get('raw_dir', 'data/raw'),
        flush_interval=config.get('data', {}).get('flush_interval_s', 300)
    )

    # 執行層
    inventory_mgr = InventoryManager(
        max_position_usd=config.get('risk', {}).get('max_position_usd', 500.0)
    )
    order_executor = OrderExecutor(
        private_key=private_key,
        token_id=token_id,
        inventory_mgr=inventory_mgr,
        trading_enabled=trading_enabled
    )
    risk_manager = RiskManager(config=config)

    # 策略層
    alpha_calc = AlphaCalculator(config=config)
    model_inference = ModelInference(
        model_dir=config.get('model', {}).get('model_dir', 'models')
    )
    model_inference.load()  # 嘗試加載模型 (找不到時自動降級到 Phase 1)

    signal_gen = SignalGenerator(
        binance=binance,
        polymarket=polymarket,
        alpha_calc=alpha_calc,
        feature_calc=feature_calc,
        model_inference=model_inference,
        config=config
    )

    maker_strategy = MakerStrategy(
        model_inference=model_inference,
        feature_calc=feature_calc,
        polymarket=polymarket,
        inventory_mgr=inventory_mgr,
        config=config
    ) if enable_maker else None

    # ----------------------------------------------------------------
    # 協程定義
    # ----------------------------------------------------------------

    async def taker_loop():
        """Taker 套利主循環 (Phase 1 + 2)"""
        await asyncio.sleep(2)  # 等待數據流初始化
        logger.info("⚡ Taker 循環已啟動")

        bankroll = config.get('strategy', {}).get('bankroll', 1000.0)
        interval = config.get('strategy', {}).get('eval_interval_ms', 100) / 1000

        while True:
            try:
                if risk_manager.is_halted():
                    await asyncio.sleep(1)
                    continue

                signal = signal_gen.evaluate(bankroll=bankroll)

                if signal:
                    # 記錄 Tick 數據
                    features = signal.get('features', {})
                    binance_data = {'price': binance.last_price, 'obi': binance.get_obi()}
                    pm_data = polymarket.get_current_price()
                    data_collector.record_tick(binance_data, pm_data, features)

                    # 執行訂單
                    result = await order_executor.place_fok_order(
                        side="BUY",
                        price=signal['price'],
                        size=signal['size'],
                        token_side=signal.get('token_side', 'YES'),
                        strategy=signal.get('strategy', 'UNKNOWN')
                    )

                    if result:
                        risk_manager.record_trade(result)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Taker 循環異常: {e}")
                await asyncio.sleep(1)

    async def data_collection_loop():
        """數據收集定時循環 (Phase 2)"""
        await data_collector.start_collection()

    # ----------------------------------------------------------------
    # 啟動所有協程
    # ----------------------------------------------------------------

    tasks = [
        asyncio.create_task(binance.connect(), name="binance_stream"),
        asyncio.create_task(polymarket.connect(), name="polymarket_stream"),
        asyncio.create_task(taker_loop(), name="taker_loop"),
        asyncio.create_task(data_collection_loop(), name="data_collector"),
    ]

    if maker_strategy:
        tasks.append(
            asyncio.create_task(
                maker_strategy.run(order_executor),
                name="maker_strategy"
            )
        )
        logger.info("🏪 Maker 做市協程已加入")

    logger.info(f"📡 共啟動 {len(tasks)} 個協程")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("收到停止信號，正在安全退出...")
    finally:
        # 安全退出
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # 強制落地所有緩衝數據
        data_collector.stop()
        await order_executor.close()
        logger.info("✅ PolyQuant-5m 已安全退出")


if __name__ == "__main__":
    asyncio.run(main())
