"""
Polymarket CLOB Websocket 實時訂單簿監聽
訂閱 market 頻道，獲取最優 Bid/Ask 報價

FIX - 3 個報價獲取缺陷:

  [1] _process_message 缺少 try/except:
      任何一條格式異常的 WS 消息會崩整個 loop
      崩潰後 5s 重連窗口內，last_update_ts 對警的舊價格仍被当成新髦

  [2] Orderbook 清空時舊價格不歸零:
      bids=[] 代表 bid 側已無掚單，應更新 best_bid=0.0
      原始代碼把 [] 當作「未傳輸」而非「清空」，舊價格一直殘留

  [3] last_update_ts 無論有沒有有效價格都更新:
      導致 is_stale 失效：完全空白的消息也會標記為「剛更新」
      修正: 只有實際写入 best_bid 或 best_ask 時才更新時間戳
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

                    await ws.send(json.dumps({
                        "auth": {},
                        "type": "market",
                        "assets_ids": [self.token_id]
                    }))
                    logger.info(f"已訂閱 Token ID: {self.token_id}")

                    async for raw in ws:
                        if not self.is_running:
                            break
                        # FIX [1]: json.loads 也包在 try 裡，防止無效 JSON 崩潰
                        try:
                            await self._process_message(json.loads(raw))
                        except Exception as e:
                            logger.warning(f"_process_message 處理失敗 (已跳過): {e} | raw={raw[:120]}")
                            # FIX [1]: 單條消息失敗不崩整個 loop，不更新 last_update_ts
                            continue

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Polymarket 連接斷開，3s 後重連...")
                # FIX [1]: 重連前重置價格，防止舊價格在重連窗口被当成新髦
                self._reset_prices()
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Polymarket Stream 異常: {e}")
                self._reset_prices()
                await asyncio.sleep(5)

    def _reset_prices(self):
        """
        FIX [1]: 重連時重置價格與時間戳
        確保重連窗口內 is_stale 會正確觸發
        """
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.last_update_ts = 0
        logger.debug("價格和時間戳已重置")

    async def close(self):
        self.is_running = False
        logger.info("Polymarket Stream 已停止")

    async def _process_message(self, data: Dict):
        """處理訂單簿快照與增量更新"""
        event_type = data.get("event_type")

        if event_type not in ("book", "price_change"):
            return

        bids = data.get("bids", None)   # FIX [2]: 用 None 而非 []，區分「未傳輸」 vs 「明確傳空」
        asks = data.get("asks", None)

        price_updated = False

        # FIX [2]: bids 是列表（包括空列表）時才處理
        if bids is not None:
            if bids:  # 有實際掚單
                raw_bid = bids[0]
                new_bid = float(raw_bid["price"]) if isinstance(raw_bid, dict) else float(raw_bid[0])
                if 0.0 < new_bid < 1.0:
                    self.best_bid = new_bid
                    price_updated = True
                else:
                    logger.warning(f"bid 價格越界 [{new_bid}]，已忧略")
            else:
                # FIX [2]: bids=[] 明確代表 bid 側已無掚單，應清零
                self.best_bid = 0.0
                price_updated = True
                logger.debug("Bid 側訂單簿已清空，重置 best_bid=0.0")

        if asks is not None:
            if asks:  # 有實際掚單
                raw_ask = asks[0]
                new_ask = float(raw_ask["price"]) if isinstance(raw_ask, dict) else float(raw_ask[0])
                if 0.0 < new_ask < 1.0:
                    self.best_ask = new_ask
                    price_updated = True
                else:
                    logger.warning(f"ask 價格越界 [{new_ask}]，已忧略")
            else:
                # FIX [2]: asks=[] 明確代表 ask 側已無掚單
                self.best_ask = 0.0
                price_updated = True
                logger.debug("Ask 側訂單簿已清空，重置 best_ask=0.0")

        # FIX [3]: 只有實際寫入價格時才更新時間戳
        # 完全空白的消息不會重置 is_stale 計時器
        if price_updated:
            self.last_update_ts = timestamp_ms()

        if self.callback and price_updated:
            await self.callback(self.get_current_price())

    def get_current_price(self) -> Dict:
        """獲取當前最優報價快照"""
        if self.best_bid > 0 and self.best_ask > 0:
            mid = (self.best_bid + self.best_ask) / 2.0
        else:
            mid = 0.0  # FIX: 0.0 而非變魔的 0.5，讓下遊過濾 < 0 檢查正確觸發
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
