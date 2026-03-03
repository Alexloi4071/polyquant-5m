"""
Binance Websocket 實時數據流
同時訂閱: AggTrade (逐筆成交) + Depth (訂單簿前20檔)
核心輸出: OBI (訂單簿失衡) + CVD (成交量差)
"""
import asyncio
import json
import time
from collections import deque
from typing import Callable, Dict, List, Optional

import websockets

from src.utils.logger import stream_logger as logger
from src.utils.helpers import pct_change, timestamp_ms


class BinanceStream:
    """
    Binance 極速 WebSocket 數據流
    同時訂閱 aggTrade 和 depth20，用於計算微觀結構特徵
    """

    def __init__(self, symbol: str = "btcusdt", callback: Optional[Callable] = None):
        self.symbol = symbol.lower()
        # 合併訂閱: 逐筆成交 + 100ms 訂單簿更新
        self.ws_url = (
            f"wss://stream.binance.com:9443/stream?streams="
            f"{self.symbol}@aggTrade/{self.symbol}@depth20@100ms"
        )
        self.callback = callback
        self.is_running = False

        # 內存緩衝區
        self.recent_trades: deque = deque(maxlen=500)  # 最近500筆成交
        self.current_orderbook: Dict = {"bids": [], "asks": [], "ts": 0}
        self.last_price: float = 0.0
        self.price_1s_ago: float = 0.0
        self._price_check_ts: int = 0

    # ------------------------------------------------------------------
    # 連接管理
    # ------------------------------------------------------------------

    async def connect(self):
        """建立 WebSocket 連接，自動重連"""
        self.is_running = True
        logger.info(f"正在連接 Binance WebSocket: {self.symbol}")

        while self.is_running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                ) as ws:
                    logger.info("✅ Binance WebSocket 已連接")
                    async for raw in ws:
                        if not self.is_running:
                            break
                        await self._process_message(json.loads(raw))

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Binance 連接斷開，3s 後重連...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Binance Stream 異常: {e}")
                await asyncio.sleep(5)

    async def close(self):
        """安全停止"""
        self.is_running = False
        logger.info("Binance Stream 已停止")

    # ------------------------------------------------------------------
    # 消息處理
    # ------------------------------------------------------------------

    async def _process_message(self, data: Dict):
        stream = data.get("stream", "")
        payload = data.get("data", {})

        if "aggTrade" in stream:
            await self._handle_trade(payload)
        elif "depth" in stream:
            self._handle_depth(payload)

        if self.callback:
            await self.callback({"stream": stream, "data": payload})

    async def _handle_trade(self, t: Dict):
        """處理逐筆成交"""
        price = float(t["p"])
        qty = float(t["q"])
        is_buyer_maker = bool(t["m"])  # True = 主動賣出

        trade = {
            "price": price,
            "qty": qty,
            "is_buyer_maker": is_buyer_maker,
            "ts": t["T"]
        }
        self.recent_trades.append(trade)
        self.last_price = price

        # 大單警報
        whale_threshold = 10.0  # BTC
        if qty >= whale_threshold:
            direction = "賣" if is_buyer_maker else "買"
            logger.warning(f"🐋 巨鯨{direction}單: {qty:.2f} BTC @ ${price:,.0f}")

        # 每秒更新 price_1s_ago
        now_ms = timestamp_ms()
        if now_ms - self._price_check_ts >= 1000:
            self.price_1s_ago = self.last_price
            self._price_check_ts = now_ms

    def _handle_depth(self, d: Dict):
        """處理訂單簿更新"""
        self.current_orderbook = {
            "bids": [[float(p), float(q)] for p, q in d.get("bids", [])[:10]],
            "asks": [[float(p), float(q)] for p, q in d.get("asks", [])[:10]],
            "ts": timestamp_ms()
        }

    # ------------------------------------------------------------------
    # 特徵計算
    # ------------------------------------------------------------------

    def get_obi(self, depth: int = 10) -> float:
        """
        訂單簿失衡 (Order Book Imbalance)
        返回 -1 到 1: 正值=買盤強, 負值=賣盤強
        """
        bids = self.current_orderbook["bids"][:depth]
        asks = self.current_orderbook["asks"][:depth]
        if not bids or not asks:
            return 0.0
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        return (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)

    def get_taker_flow(self, window: int = 100) -> Dict:
        """
        主動成交量差 (Cumulative Volume Delta)
        Returns: buy_vol, sell_vol, delta, buy_ratio
        """
        trades = list(self.recent_trades)[-window:]
        buy_vol = sum(t["qty"] for t in trades if not t["is_buyer_maker"])
        sell_vol = sum(t["qty"] for t in trades if t["is_buyer_maker"])
        return {
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "delta": buy_vol - sell_vol,
            "buy_ratio": buy_vol / (buy_vol + sell_vol + 1e-9)
        }

    def get_1s_price_change(self) -> float:
        """過去1秒的價格變化率"""
        if self.price_1s_ago == 0:
            return 0.0
        return pct_change(self.price_1s_ago, self.last_price)

    def get_whale_alert(self, threshold_btc: float = 10.0) -> Optional[Dict]:
        """檢查最近一筆成交是否為大單"""
        if not self.recent_trades:
            return None
        last = self.recent_trades[-1]
        if last["qty"] >= threshold_btc:
            return last
        return None
