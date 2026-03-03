"""
輔助工具函數
"""
import time
from typing import Optional


def timestamp_ms() -> int:
    """獲取當前毫秒級時間戳"""
    return int(time.time() * 1000)


def safe_float(value, default: float = 0.0) -> float:
    """安全類型轉換"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct_change(old: float, new: float) -> float:
    """計算百分比變化"""
    if old == 0:
        return 0.0
    return (new - old) / old
