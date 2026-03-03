"""
Polymarket 訂單執行引擎
使用 py-clob-client 進行 EIP-712 簽名與 FOK 下單
"""
from typing import Dict, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

from src.utils.logger import trade_logger as logger


class OrderExecutor:
    """Polymarket 訂單執行器"""

    def __init__(self, private_key: str, host: str = "https://clob.polymarket.com",
                 chain_id: int = 137):
        """
        Args:
            private_key: Polygon 錢包私鑰 (0x 開頭的 64 位十六進制)
            host:        Polymarket CLOB API endpoint
            chain_id:    137 = Polygon Mainnet
        """
        self.private_key = private_key
        self.host = host
        self.chain_id = chain_id
        self.client: Optional[ClobClient] = None
        self._init_client()

    def _init_client(self):
        """初始化並認證 CLOB 客戶端"""
        try:
            self.client = ClobClient(
                self.host,
                key=self.private_key,
                chain_id=self.chain_id
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            logger.info("✅ Polymarket CLOB 客戶端初始化成功")
        except Exception as e:
            logger.error(f"❌ CLOB 客戶端初始化失敗: {e}")
            raise

    def execute_fok(self, token_id: str, price: float, size: float,
                    side: str = "BUY") -> Dict:
        """
        提交 FOK (Fill-Or-Kill) 訂單
        FOK: 全部成交或全部取消，不留掛單

        Args:
            token_id: YES 或 NO Token 的 ID
            price:    價格 (0~1)
            size:     下單金額 (USD USDC)
            side:     "BUY" 或 "SELL"

        Returns:
            {"success": bool, "order_id": str, "error": str}
        """
        if self.client is None:
            return {"success": False, "error": "Client not initialized"}

        try:
            order_args = OrderArgs(price=price, size=size, side=side, token_id=token_id)
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.FOK)

            order_id = resp.get("orderID", "unknown") if resp else "unknown"
            logger.info(
                f"✅ FOK 訂單已提交: {side} ${size:.2f} @ {price:.4f} | ID={order_id}"
            )
            return {"success": True, "order_id": order_id, "response": resp}

        except Exception as e:
            logger.error(f"❌ 訂單執行失敗: {e}")
            return {"success": False, "order_id": None, "error": str(e)}

    def get_balance(self) -> float:
        """獲取當前 USDC 餘額"""
        try:
            balance = self.client.get_balance()
            return float(balance)
        except Exception as e:
            logger.error(f"獲取餘額失敗: {e}")
            return 0.0
