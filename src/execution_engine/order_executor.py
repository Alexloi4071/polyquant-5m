"""
訂單執行引擎 - Phase 1 (FOK Taker) + Phase 4 (Limit Maker)
使用官方 py-clob-client 處理 Polymarket CLOB API 完整認證流程

FIX - 原始代碼存在 4 個致命錯誤:

  [1] 簽名算法完全錯誤:
      原: HMAC-SHA256(private_key, json.dumps(payload))
      正: EIP-712 訂單簽名 (on-chain) + L2 HMAC(api_secret, ts+method+path+body)
      Polymarket CLOB 使用兩層認證架構 (L1 獲取 API key，L2 簽名每個請求)

  [2] Ethereum 地址派生錯誤:
      原: self.private_key[:42]  — 純粹字符串截取，完全無效
      正: Account.from_key(key).address  — 經 keccak256 派生的真實地址

  [3] 缺少 EIP-712 訂單簽名:
      Polymarket 訂單必須用私鑰做 EIP-712 on-chain 簽名才有效
      原始代碼提交的是未簽名的訂單內容

  [4] 將同步 py-clob-client 操作包装為非阻塞 async
      使用 asyncio.get_event_loop().run_in_executor() 防止阻塞事件循環
"""
import asyncio
import time
from typing import Dict, Optional

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, BUY, SELL
from py_clob_client.exceptions import PolyApiException

from src.execution_engine.inventory_manager import InventoryManager
from src.utils.logger import trade_logger as logger

CHAIN_ID = 137  # Polygon Mainnet


class OrderExecutor:
    """
    Polymarket CLOB API 訂單執行器 (修正版)

    認證架構:
    - L1: 以 EIP-712 簽名獲取 API key / secret / passphrase
    - L2: 用 API secret 做 HMAC-SHA256(timestamp+method+path+body) 簽名每個請求
    - 訂單本身用 EIP-712 typed data 簽名 (on-chain 有效性)
    以上全部由 py-clob-client 官方幫幫處理
    """

    def __init__(
        self,
        private_key: str,
        token_id: str,
        inventory_mgr: InventoryManager,
        trading_enabled: bool = False
    ):
        self.token_id = token_id
        self.inventory = inventory_mgr
        self.trading_enabled = trading_enabled
        self._private_key = private_key

        # FIX [2]: 正確派生 Ethereum 地址 (keccak256，非字符串截取)
        self.address = Account.from_key(private_key).address
        logger.info(f"Polymarket 錐包地址: {self.address}")

        self._client: Optional[ClobClient] = None

    def _get_client(self) -> ClobClient:
        """
        延遲初始化 ClobClient
        首次呼叫時發起 L1 簽名，獲取 API credentials
        """
        if self._client is None:
            # FIX [1][3]: py-clob-client 自動處理:
            # - L1 EIP-712 簽名 → 獲取 api_key / secret / passphrase
            # - 每個請求的 L2 HMAC 簽名
            # - 訂單的 EIP-712 on-chain 簽名
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                key=self._private_key,
                chain_id=CHAIN_ID
            )
            try:
                api_creds = self._client.create_or_derive_api_creds()
                self._client.set_api_creds(api_creds)
                logger.info("\u2705 Polymarket L1/L2 API \u8a8d\u8b49\u5b8c\u6210")
            except Exception as e:
                logger.error(f"Polymarket API \u8a8d\u8b49\u5931\u6557: {e}")
                raise
        return self._client

    # ------------------------------------------------------------------
    # Phase 1/2: FOK \u8a02\u55ae (Taker)
    # ------------------------------------------------------------------

    async def place_fok_order(
        self,
        side: str,
        price: float,
        size: float,
        token_side: str = "YES",
        strategy: str = "UNKNOWN"
    ) -> Optional[Dict]:
        """FOK (Fill or Kill) \u8a02\u55ae"""
        if not self.trading_enabled:
            logger.info(f"[PAPER] FOK {side} {size:.2f} {token_side} @ {price:.4f} [{strategy}]")
            fake_id = f"paper_{int(time.time()*1000)}"
            self.inventory.on_fill(fake_id, strategy, token_side, side, size, price)
            return {"order_id": fake_id, "status": "paper_filled",
                    "price": price, "size": size, "pnl": 0.0}

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._place_order_sync,
                OrderType.FOK, side, price, size, strategy, token_side
            )
        except Exception as e:
            logger.error(f"FOK \u4e0b\u55ae\u7570\u5e38: {e}")
            return None

    # ------------------------------------------------------------------
    # Phase 4: Limit GTC \u639a\u55ae (Maker)
    # ------------------------------------------------------------------

    async def place_limit_order(
        self,
        side: str,
        price: float,
        size: float,
        token_side: str = "YES",
        strategy: str = "MAKER"
    ) -> Optional[Dict]:
        """GTC \u9650\u50f9\u639a\u55ae"""
        if not self.trading_enabled:
            logger.info(f"[PAPER] LIMIT {side} {size:.2f} {token_side} @ {price:.4f} [{strategy}]")
            fake_id = f"paper_limit_{int(time.time()*1000)}"
            return {"order_id": fake_id, "status": "paper_open",
                    "side": side, "price": price, "size": size, "token_side": token_side}

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._place_order_sync,
                OrderType.GTC, side, price, size, strategy, token_side
            )
        except Exception as e:
            logger.error(f"Limit \u4e0b\u55ae\u7570\u5e38: {e}")
            return None

    # ------------------------------------------------------------------
    # \u5171\u7528同\u6b65\u4e0b\u55ae方\u6cd5
    # ------------------------------------------------------------------

    def _place_order_sync(
        self,
        order_type: OrderType,
        side: str,
        price: float,
        size: float,
        strategy: str,
        token_side: str
    ) -> Optional[Dict]:
        """
        FIX [1][3]: \u540c\u6b65\u4e0b\u55ae\uff0c\u5728 executor \u7dda\u7a0b\u4e2d\u57f7\u884c
        py-clob-client \u6703\u81ea\u52d5\u5b8c\u6210:
          1. EIP-712 \u7c3d\u540d\u8a02\u55ae (create_order)
          2. L2 HMAC \u7c3d\u540d\u8acb\u6c42\u982d (post_order)
        """
        try:
            client = self._get_client()
            side_enum = BUY if side.upper() == "BUY" else SELL
            type_label = "FOK" if order_type == OrderType.FOK else "GTC"

            order_args = OrderArgs(
                token_id=self.token_id,
                price=round(price, 4),
                size=round(size, 2),
                side=side_enum,
                fee_rate_bps=0,
                nonce=0,
                expiration=0
            )

            # FIX [3]: create_order() \u5167\u90e8\u57f7\u884c EIP-712 typed data \u7c3d\u540d
            signed_order = client.create_order(order_args)

            # FIX [1]: post_order() \u5167\u90e8\u57f7\u884c L2 HMAC \u8a8d\u8b49
            resp = client.post_order(signed_order, order_type)

            if resp and resp.get("success"):
                order_id = resp.get("orderID", "")
                logger.info(
                    f"\u2705 {type_label} {side} {size:.2f} {token_side} "
                    f"@ {price:.4f} | ID: {order_id}"
                )
                self.inventory.on_fill(order_id, strategy, token_side, side, size, price)
                return {"order_id": order_id, "status": "filled" if order_type == OrderType.FOK else "open",
                        "price": price, "size": size, "pnl": 0.0}
            else:
                logger.warning(f"{type_label} \u8a02\u55ae\u62d2\u7d55: {resp}")
                return None

        except PolyApiException as e:
            logger.error(f"Polymarket API \u932f\u8aa4: HTTP {e.status_code} - {e.error_msg}")
            return None
        except Exception as e:
            logger.error(f"\u4e0b\u55ae\u540c\u6b65\u7570\u5e38: {e}")
            return None

    # ------------------------------------------------------------------
    # \u649c\u55ae / \u67e5\u8a62
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str) -> bool:
        """\u649c\u92b7\u6307\u5b9a\u8a02\u55ae"""
        if not self.trading_enabled:
            logger.info(f"[PAPER] \u649c\u55ae: {order_id}")
            return True
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._cancel_sync, order_id)
        except Exception as e:
            logger.error(f"\u649c\u55ae\u7570\u5e38 {order_id}: {e}")
            return False

    def _cancel_sync(self, order_id: str) -> bool:
        try:
            client = self._get_client()
            resp = client.cancel(order_id)
            canceled = resp.get("canceled", [])
            success = order_id in canceled if isinstance(canceled, list) else bool(canceled)
            if success:
                logger.info(f"\u2705 \u5df2\u649c\u55ae: {order_id}")
            return success
        except PolyApiException as e:
            logger.error(f"\u649c\u55ae API \u932f\u8aa4: {e.status_code} - {e.error_msg}")
            return False

    async def get_order_status(self, order_id: str) -> Optional[Dict]:
        """\u67e5\u8a62\u8a02\u55ae\u72c0\u614b"""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._get_status_sync, order_id)
        except Exception as e:
            logger.error(f"\u67e5\u8a62\u8a02\u55ae\u7570\u5e38: {e}")
            return None

    def _get_status_sync(self, order_id: str) -> Optional[Dict]:
        try:
            return self._get_client().get_order(order_id)
        except Exception as e:
            logger.error(f"\u67e5\u8a62\u8a02\u55ae\u540c\u6b65\u7570\u5e38: {e}")
            return None

    async def close(self):
        """\u91cb\u653e\u8cc7\u6e90"""
        logger.info("OrderExecutor \u5df2\u95dc\u9589")
