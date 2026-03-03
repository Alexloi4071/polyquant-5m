"""
Polymarket CLOB Websocket 實時訂單簿監聽
訂閱 market 頻道，獲取最優 Bid/Ask 報價
"""
import asyncio
import json
from typing import Callable, Dict, Optional

import websockets

from src.utils.logger import stream_logger as logger
from src.utils.helpers import timestamp_ms


class PolymarketStream:
    """Polymarket CLOB 訂單簿實時監聽器"""

    def __init__(self, token_id: str, callback: Optional[Callable] = None):
        self.token_id = token_id
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.callback = callback
        self.is_running = False

        # 當前最優報價
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_update_ts: int = 0

    async def connect(self):
        """建立 WebSocket 連接並訂閱市場，自動重連"""
        self.is_running = True
        logger.info("正在連接 Polymarket CLOB WebSocket...")

        while self.is_running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10
                ) as ws:
                    logger.info("✅ Polymarket WebSocket 已連接")

                    # 訂閱市場訂單簿
                    await ws.send(json.dumps({
                        "auth": {},
                        "type": "market",
                        "assets_ids": [self.token_id]
                    }))
                    logger.info(f"已訂閱 Token ID: {self.token_id}")

                    async for raw in ws:
                        if not self.is_running:
                            break
                        await self._process_message(json.loads(raw))

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Polymarket 連接斷開，3s 後重連...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Polymarket Stream 異常: {e}")
                await asyncio.sleep(5)

    async def close(self):
        self.is_running = False
        logger.info("Polymarket Stream 已停止")

    async def _process_message(self, data: Dict):
        """處理訂單簿快照與增量更新"""
        event_type = data.get("event_type")

        if event_type in ("book", "price_change"):
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if bids:
                self.best_bid = float(bids[0]["price"]) if isinstance(bids[0], dict) else float(bids[0][0])
            if asks:
                self.best_ask = float(asks[0]["price"]) if isinstance(asks[0], dict) else float(asks[0][0])

            self.last_update_ts = timestamp_ms()

            if self.callback:
                await self.callback(self.get_current_price())

    def get_current_price(self) -> Dict:
        """獲取當前最優報價快照"""
        mid = (self.best_bid + self.best_ask) / 2.0 if self.best_bid and self.best_ask else 0.5
        return {
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": mid,
            "spread": self.best_ask - self.best_bid,
            "last_update_ts": self.last_update_ts
        }

    @property
    def is_stale(self) -> bool:
        """檢查報價是否超過 5 秒未更新（數據陳舊）"""
        return (timestamp_ms() - self.last_update_ts) > 5000
