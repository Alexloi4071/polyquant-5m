"""
專業日誌配置模塊
使用 Loguru 進行結構化日誌記錄
三種日誌分類: 交易日誌、數據流日誌、錯誤日誌
"""
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_level: str = "INFO"):
    """
    配置項目日誌系統

    Args:
        log_level: 日誌級別 (DEBUG, INFO, WARNING, ERROR)
    """
    logger.remove()  # 移除默認處理器

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # 控制台輸出
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True
    )

    # 交易執行日誌
    logger.add(
        log_dir / "trading.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        level="INFO",
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        filter=lambda record: "TRADE" in record["extra"]
    )

    # 數據流日誌
    logger.add(
        log_dir / "data_stream.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
        level="DEBUG",
        rotation="500 MB",
        retention="7 days",
        compression="zip",
        filter=lambda record: "STREAM" in record["extra"]
    )

    # 錯誤日誌 (WARNING 及以上)
    logger.add(
        log_dir / "errors.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}\n{exception}",
        level="WARNING",
        rotation="50 MB",
        retention="90 days",
        backtrace=True,
        diagnose=True
    )

    return logger


# 模塊級別的專用 logger
trade_logger = logger.bind(TRADE=True)
stream_logger = logger.bind(STREAM=True)
