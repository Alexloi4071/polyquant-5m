"""
Tick 數據收集器
將 Binance 和 Polymarket 的實時數據落地存儲，用於後續模型訓練
"""
import asyncio
import csv
from pathlib import Path
from typing import Dict
from collections import deque

from src.utils.logger import logger
from src.utils.helpers import timestamp_ms


class DataCollector:
    """實時 Tick 數據收集器"""

    def __init__(self, data_dir: str = "data/raw", buffer_size: int = 1000, 
                 flush_interval: int = 300):
        """
        Args:
            data_dir: 數據存儲目錄
            buffer_size: 內存緩衝區大小 (條數)
            flush_interval: 落盤間隔 (秒)
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        
        # 內存緩衝
        self.tick_buffer = deque(maxlen=buffer_size)
        self.last_flush_time = timestamp_ms()
        
        self.is_collecting = False
    
    def record_tick(self, binance_data: Dict, polymarket_data: Dict, features: Dict):
        """
        記錄單個 Tick 快照
        
        Args:
            binance_data: Binance 當前狀態 {price, obi, cvd, ...}
            polymarket_data: Polymarket 當前報價 {best_bid, best_ask, ...}
            features: 計算好的特徵向量
        """
        tick = {
            'timestamp': timestamp_ms(),
            'binance_price': binance_data.get('price', 0),
            'binance_obi': binance_data.get('obi', 0),
            'polymarket_bid': polymarket_data.get('best_bid', 0),
            'polymarket_ask': polymarket_data.get('best_ask', 0),
            **features  # 展開所有特徵
        }
        
        self.tick_buffer.append(tick)
        
        # 自動落盤
        if self._should_flush():
            self.flush_to_disk()
    
    def _should_flush(self) -> bool:
        """判斷是否需要落盤"""
        return (
            len(self.tick_buffer) >= self.buffer_size or
            (timestamp_ms() - self.last_flush_time) >= (self.flush_interval * 1000)
        )
    
    def flush_to_disk(self):
        """將緩衝區數據寫入 CSV"""
        if not self.tick_buffer:
            return
        
        # 文件命名: ticks_YYYYMMDD_HHMMSS.csv
        import time
        filename = f"ticks_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = self.data_dir / filename
        
        try:
            # 獲取所有欄位名稱
            fieldnames = list(self.tick_buffer[0].keys())
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.tick_buffer)
            
            logger.info(f"📝 已落盤 {len(self.tick_buffer)} 條 Tick 數據 -> {filename}")
            self.tick_buffer.clear()
            self.last_flush_time = timestamp_ms()
            
        except Exception as e:
            logger.error(f"數據落盤失敗: {e}")
    
    async def start_collection(self):
        """啟動數據收集協程（定時刷盤）"""
        self.is_collecting = True
        logger.info("數據收集器已啟動")
        
        while self.is_collecting:
            await asyncio.sleep(self.flush_interval)
            if self.tick_buffer:
                self.flush_to_disk()
    
    def stop(self):
        """停止收集並強制刷盤"""
        self.is_collecting = False
        if self.tick_buffer:
            self.flush_to_disk()
        logger.info("數據收集器已停止")
