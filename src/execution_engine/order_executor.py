"""
訂單執行引擎 - Phase 1 (FOK Taker) + Phase 4 (Limit Maker)
Polymarket CLOB 下單、撤單、查詢
"""
import asyncio
import hashlib
import hmac
import json
import time
from typing import Dict, Optional

import aiohttp

from src.execution_engine.inventory_manager import InventoryManager
from src.utils.logger import trade_logger as logger


class OrderExecutor:
    """
    Polymarket CLOB API 訂單執行器

    支持:
    - FOK 限時訂單 (Phase 1/2 Taker 套利)
    - Limit GTC 掛單 (Phase 4 Maker 做市)
    - 撤單
    - 訂單狀態查詢
    """

    CLOB_API = "https://clob.polymarket.com"

    def __init__(
        self,
        private_key: str,
        token_id: str,
        inventory_mgr: InventoryManager,
        trading_enabled: bool = False
    ):
        self.private_key = private_key
        self.token_id = token_id
        self.inventory = inventory_mgr
        self.trading_enabled = trading_enabled
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # 簽名工具
    # ------------------------------------------------------------------

    def _sign_payload(self, payload: dict) -> str:
        """對訂單 payload 進行 HMAC-SHA256 簽名"""
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        signature = hmac.new(
            self.private_key.encode(),
            body.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _build_headers(self, payload: dict) -> dict:
        """構建帶簽名的請求頭"""
        signature = self._sign_payload(payload)
        return {
            "Content-Type": "application/json",
            "POLY_ADDRESS": self.private_key[:42],  # 前 42 位作為地址
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(int(time.time() * 1000)),
            "POLY_NONCE": str(int(time.time() * 1000000))
        }

    # ------------------------------------------------------------------
    # Phase 1/2: FOK 市場訂單 (Taker)
    # ------------------------------------------------------------------

    async def place_fok_order(
        self,
        side: str,
        price: float,
        size: float,
        token_side: str = "YES",
        strategy: str = "UNKNOWN"
    ) -> Optional[Dict]:
        """
        FOK (Fill or Kill) 訂單
        即時完全成交或全部撤銷，用於套利策略

        Args:
            side: BUY / SELL
            price: 限價 (0~1)
            size: 數量 (USD)
            token_side: YES / NO token
            strategy: 來源策略名稱
        """
        if not self.trading_enabled:
            logger.info(f"[PAPER] FOK {side} {size:.2f} {token_side} @ {price:.4f} [{strategy}]")
            # Paper trading: 模擬成交回調
            fake_order_id = f"paper_{int(time.time()*1000)}"
            self.inventory.on_fill(fake_order_id, strategy, token_side, side, size, price)
            return {'order_id': fake_order_id, 'status': 'paper_filled', 'price': price, 'size': size}

        payload = {
            "token_id": self.token_id,
            "side": side.upper(),
            "type": "FOK",
            "price": str(round(price, 4)),
            "size": str(round(size, 2)),
            "timestamp": int(time.time() * 1000)
        }

        try:
            session = await self._get_session()
            headers = self._build_headers(payload)
            async with session.post(
                f"{self.CLOB_API}/order",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                data = await resp.json()

                if resp.status == 200 and data.get('success'):
                    order_id = data.get('orderID', '')
                    logger.info(f"✅ FOK 成交 {side} {size:.2f} {token_side} @ {price:.4f} | ID: {order_id}")
                    self.inventory.on_fill(order_id, strategy, token_side, side, size, price)
                    return {'order_id': order_id, 'status': 'filled', 'price': price, 'size': size}
                else:
                    logger.warning(f"FOK 訂單拒絕: {data}")
                    return None

        except asyncio.TimeoutError:
            logger.warning("FOK 訂單超時 (>3s)")
            return None
        except Exception as e:
            logger.error(f"FOK 下單異常: {e}")
            return None

    # ------------------------------------------------------------------
    # Phase 4: Limit GTC 掛單 (Maker)
    # ------------------------------------------------------------------

    async def place_limit_order(
        self,
        side: str,
        price: float,
        size: float,
        token_side: str = "YES",
        strategy: str = "MAKER"
    ) -> Optional[Dict]:
        """
        GTC (Good Till Cancel) 限價掛單
        用於 Maker 做市策略

        Args:
            side: BUY / SELL
            price: 限價 (0~1)
            size: 數量 (USD)
            token_side: YES / NO token
            strategy: 來源策略名稱
        """
        if not self.trading_enabled:
            logger.info(f"[PAPER] LIMIT {side} {size:.2f} {token_side} @ {price:.4f} [{strategy}]")
            fake_order_id = f"paper_limit_{int(time.time()*1000)}"
            return {
                'order_id': fake_order_id,
                'status': 'paper_open',
                'side': side,
                'price': price,
                'size': size,
                'token_side': token_side
            }

        payload = {
            "token_id": self.token_id,
            "side": side.upper(),
            "type": "GTC",
            "price": str(round(price, 4)),
            "size": str(round(size, 2)),
            "timestamp": int(time.time() * 1000)
        }

        try:
            session = await self._get_session()
            headers = self._build_headers(payload)
            async with session.post(
                f"{self.CLOB_API}/order",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()

                if resp.status == 200 and data.get('success'):
                    order_id = data.get('orderID', '')
                    logger.info(f"📋 Limit 掛單 {side} {size:.2f} {token_side} @ {price:.4f} | ID: {order_id}")
                    return {
                        'order_id': order_id,
                        'status': 'open',
                        'side': side,
                        'price': price,
                        'size': size,
                        'token_side': token_side
                    }
                else:
                    logger.warning(f"Limit 掛單拒絕: {data}")
                    return None

        except Exception as e:
            logger.error(f"Limit 下單異常: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """撤銷指定訂單"""
        if not self.trading_enabled:
            logger.info(f"[PAPER] 撤單: {order_id}")
            return True

        payload = {"orderID": order_id}
        try:
            session = await self._get_session()
            headers = self._build_headers(payload)
            async with session.delete(
                f"{self.CLOB_API}/order",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()
                success = data.get('success', False)
                if success:
                    logger.info(f"✅ 已撤單: {order_id}")
                return success
        except Exception as e:
            logger.error(f"撤單異常 {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> Optional[Dict]:
        """查詢訂單狀態"""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.CLOB_API}/order/{order_id}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"查詢訂單異常: {e}")
            return None
