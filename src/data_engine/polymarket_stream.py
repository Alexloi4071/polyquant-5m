"""
Polymarket CLOB 專業級數據流 (重構版)
對齊官方最新 API 結構，加入 Sequence 校驗、Tick Size 處理與 Binance-Oracle 偏移修正。
"""
import asyncio
import json
import aiohttp
from typing import Callable, Dict, Optional, List

import websockets

from src.utils.logger import stream_logger as logger
from src.utils.helpers import timestamp_ms


class PolymarketStream:
    """
    Polymarket CLOB 數據引擎 - 專業修復版
    解決了原版代碼中 price_change 結構錯誤、缺少 Sequence 校驗、忽略 Tick Size 等致命問題。
    """

    def __init__(
        self, 
        token_id: str, 
        binance_stream=None,
        oracle_client=None,
        callback: Optional[Callable] = None
    ):
        self.token_id = token_id
        self.binance_stream = binance_stream
        self.oracle_client = oracle_client
        self.callback = callback
        
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.rest_url = "https://clob.polymarket.com/book"
        self.is_running = False

        # 核心數據狀態
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_sequence: int = 0
        self.last_update_ts: int = 0
        self.tick_size: float = 0.01  # 默認 0.01
        
        # 基準價與偏差
        self.price_to_beat: Optional[float] = None
        self.oracle_bias: float = 0.0  # Binance 與 Oracle 的預期偏差

    async def connect(self):
        """建立 WebSocket 連接，支持自動重連與 Sequence 校驗"""
        self.is_running = True
        logger.info("正在啟動專業級 Polymarket WebSocket...")

        while self.is_running:
            try:
                # 啟動前先通過 REST 獲取一次 Snapshot 以初始化 Sequence
                await self._fetch_snapshot()

                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10
                ) as ws:
                    logger.info("✅ Polymarket WebSocket 已連接")

                    # 訂閱市場，開啟 custom_feature_enabled 以獲取 best_bid_ask 事件
                    subscribe_msg = {
                        "auth": {},
                        "type": "market",
                        "assets_ids": [self.token_id],
                        "custom_feature_enabled": True  # 獲取官方最乾淨的 best_bid_ask 事件
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"已訂閱 Token: {self.token_id} (已開啟高級特性)")

                    async for raw in ws:
                        if not self.is_running:
                            break
                        try:
                            data = json.loads(raw)
                            await self._process_message(data)
                        except Exception as e:
                            logger.error(f"解析消息失敗: {e} | raw={raw[:100]}")

            except Exception as e:
                logger.warning(f"WebSocket 連接異常: {e}，5s 後重連...")
                self._reset_state()
                await asyncio.sleep(5)

    async def _fetch_snapshot(self):
        """通過 REST API 獲取訂單簿快照，用於初始化或修復 Sequence Gap"""
        try:
            async with aiohttp.ClientSession() as session:
                params = {"token_id": self.token_id}
                async with session.get(self.rest_url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # 更新數據
                        bids = data.get("bids", [])
                        asks = data.get("asks", [])
                        if bids: self.best_bid = float(bids[0]["price"])
                        if asks: self.best_ask = float(asks[0]["price"])
                        
                        # 重要：同步 Sequence
                        self.last_sequence = int(data.get("last_sequence", 0))
                        self.last_update_ts = timestamp_ms()
                        logger.info(f"📋 已同步訂單簿快照 | Seq: {self.last_sequence} | Bid: {self.best_bid} | Ask: {self.best_ask}")
        except Exception as e:
            logger.error(f"獲取快照失敗: {e}")

    def _reset_state(self):
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.last_sequence = 0
        self.last_update_ts = 0

    async def _process_message(self, data: Dict):
        """處理官方最新的事件結構"""
        event_type = data.get("event_type")
        
        # 1. Sequence 校驗
        new_seq = int(data.get("last_sequence", 0))
        if new_seq > 0:
            if self.last_sequence > 0 and new_seq > self.last_sequence + 1:
                logger.warning(f"⚠️ 檢測到 Sequence Gap! (Local: {self.last_sequence}, Remote: {new_seq})，觸發快照重置...")
                await self._fetch_snapshot()
                return
            self.last_sequence = new_seq

        # 2. 處理不同事件類型
        updated = False

        # A. 官方最推薦的 best_bid_ask 事件 (custom_feature_enabled=True)
        if event_type == "best_bid_ask":
            self.best_bid = float(data.get("best_bid", 0))
            self.best_ask = float(data.get("best_ask", 0))
            updated = True

        # B. 修正後的 price_change 解析 (處理嵌套數組)
        elif event_type == "price_change":
            changes = data.get("price_changes", [])
            for change in changes:
                if change.get("asset_id") == self.token_id:
                    self.best_bid = float(change.get("best_bid", 0))
                    self.best_ask = float(change.get("best_ask", 0))
                    updated = True
                    break

        # C. 處理 Tick Size 變化 (對齊文檔：價格 > 0.96 或 < 0.04 時精度變為 0.001)
        if updated:
            self._update_tick_size()
            self.last_update_ts = timestamp_ms()
            if self.callback:
                await self.callback(self.get_current_price())

    def _update_tick_size(self):
        """根據價格區間自動調整 Tick Size"""
        price = (self.best_bid + self.best_ask) / 2.0
        if price > 0.96 or price < 0.04:
            self.tick_size = 0.001
        else:
            self.tick_size = 0.01

    def get_current_price(self) -> Dict:
        """獲取最優報價，並集成 Binance-Oracle 基準價修正"""
        mid = (self.best_bid + self.best_ask) / 2.0 if self.best_bid and self.best_ask else 0.5
        
        # 獲取基準價 (Price to Beat)
        benchmark = self._estimate_benchmark()

        return {
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": mid,
            "spread": self.best_ask - self.best_bid,
            "tick_size": self.tick_size,
            "price_to_beat": benchmark,
            "last_update_ts": self.last_update_ts,
            "is_stale": self.is_stale
        }

    def _estimate_benchmark(self) -> float:
        """
        核心邏輯：Binance-Oracle 偏移修正
        如果 Polymarket 基準價為空，使用 Binance 價格 + 歷史偏差進行估算
        """
        if self.price_to_beat is not None:
            return self.price_to_beat
        
        # 如果 Polymarket 沒給基準價，嘗試從 Binance 估算
        if self.binance_stream and self.binance_stream.last_price > 0:
            # 估算公式: Binance 實時價 + (Oracle 與 Binance 的預期價差)
            return self.binance_stream.last_price + self.oracle_bias
            
        return 0.0 # 徹底缺失數據時返回 0

    @property
    def is_stale(self) -> bool:
        return (timestamp_ms() - self.last_update_ts) > 3000  # 縮短到 3s 以應對 5m 高頻
