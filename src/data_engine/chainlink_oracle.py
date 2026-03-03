"""
Chainlink Oracle 價格獲取器 - Phase 3
從 Polygon 鏈上讀取 BTC/USD Chainlink 喂價
用於生成精確的 Polymarket 結算 Label

合約地址 (Polygon Mainnet):
  BTC/USD: 0xc907E116054Ad103354f2D350FD2514433D57F6f
"""
import asyncio
import json
from typing import Optional, List, Dict
from datetime import datetime, timedelta

import aiohttp

from src.utils.logger import logger

# Chainlink BTC/USD Aggregator (Polygon)
CHAINLINK_BTC_USD_POLYGON = "0xc907E116054Ad103354f2D350FD2514433D57F6f"

# Polygon RPC 節點 (免費公共節點)
POLYGON_RPC_URLS = [
    "https://polygon-rpc.com",
    "https://rpc-mainnet.matic.network",
    "https://matic-mainnet.chainstacklabs.com"
]

# Chainlink Aggregator ABI (只需 latestRoundData + getRoundData)
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "_roundId", "type": "uint80"}],
        "name": "getRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]


class ChainlinkOracle:
    """
    Chainlink BTC/USD Oracle 讀取器
    
    使用 eth_call 通過 JSON-RPC 直接讀取鏈上數據
    不需要私鑰，只讀操作
    """

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url or POLYGON_RPC_URLS[0]
        self.contract_address = CHAINLINK_BTC_USD_POLYGON
        self.decimals = 8  # Chainlink BTC/USD 精度為 8 位
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # 核心 RPC 調用
    # ------------------------------------------------------------------

    async def _eth_call(self, data: str) -> Optional[str]:
        """執行 eth_call"""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {"to": self.contract_address, "data": data},
                "latest"
            ],
            "id": 1
        }

        # 嘗試多個 RPC 節點
        for rpc_url in POLYGON_RPC_URLS:
            try:
                session = await self._get_session()
                async with session.post(
                    rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result = await resp.json()
                    if 'result' in result:
                        return result['result']
            except Exception as e:
                logger.warning(f"RPC {rpc_url} 失敗: {e}")
                continue

        logger.error("所有 Polygon RPC 節點均不可用")
        return None

    def _decode_latest_round(self, hex_result: str) -> Optional[Dict]:
        """解碼 latestRoundData 返回值"""
        try:
            # 去除 0x 前綴
            data = hex_result[2:] if hex_result.startswith('0x') else hex_result
            # 每個 uint256/int256 佔 32 bytes = 64 hex chars
            chunks = [data[i:i+64] for i in range(0, len(data), 64)]
            if len(chunks) < 5:
                return None

            round_id = int(chunks[0], 16)
            answer = int(chunks[1], 16)
            # int256 有符號處理
            if answer >= 2**255:
                answer -= 2**256
            started_at = int(chunks[2], 16)
            updated_at = int(chunks[3], 16)

            price_usd = answer / (10 ** self.decimals)

            return {
                'round_id': round_id,
                'price_usd': price_usd,
                'started_at': started_at,
                'updated_at': updated_at,
                'updated_at_dt': datetime.utcfromtimestamp(updated_at)
            }
        except Exception as e:
            logger.error(f"Oracle 數據解碼失敗: {e}")
            return None

    # ------------------------------------------------------------------
    # 公開接口
    # ------------------------------------------------------------------

    async def get_latest_price(self) -> Optional[Dict]:
        """獲取最新 BTC/USD Oracle 價格"""
        # latestRoundData() selector = 0xfeaf968c
        result = await self._eth_call("0xfeaf968c")
        if result is None:
            return None
        return self._decode_latest_round(result)

    async def get_price_at_timestamp(self, target_ts: int, tolerance_s: int = 300) -> Optional[float]:
        """
        獲取最接近指定時間戳的 Oracle 價格

        Args:
            target_ts: 目標 Unix 時間戳 (秒)
            tolerance_s: 允許的時間誤差 (秒)，默認 5 分鐘

        Returns:
            BTC 價格 (USD)，找不到返回 None
        """
        latest = await self.get_latest_price()
        if latest is None:
            return None

        # 如果最新價格就在目標時間附近
        if abs(latest['updated_at'] - target_ts) <= tolerance_s:
            return latest['price_usd']

        # 向前搜索歷史 Round
        latest_round_id = latest['round_id']

        # Chainlink 每個 phase 最多 2^16 個 round
        phase_id = latest_round_id >> 64
        aggregator_round = latest_round_id & 0xFFFFFFFFFFFFFFFF

        # 二分搜索最近 round
        low, high = max(1, aggregator_round - 1000), aggregator_round
        best_round = None
        best_diff = float('inf')

        for _ in range(20):  # 最多 20 次迭代
            if low > high:
                break
            mid = (low + high) // 2
            round_id = (phase_id << 64) | mid

            # getRoundData selector = 0x9a6fc8f5
            padded = f"{round_id:064x}"
            result = await self._eth_call(f"0x9a6fc8f5{padded}")
            if result is None:
                break

            decoded = self._decode_latest_round(result)
            if decoded is None:
                break

            diff = abs(decoded['updated_at'] - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_round = decoded

            if decoded['updated_at'] < target_ts:
                low = mid + 1
            else:
                high = mid - 1

        if best_round and best_diff <= tolerance_s:
            return best_round['price_usd']

        logger.warning(f"找不到時間戳 {target_ts} 附近的 Oracle 價格 (最近差距: {best_diff}s)")
        return None


if __name__ == "__main__":
    async def test():
        oracle = ChainlinkOracle()
        price = await oracle.get_latest_price()
        print(f"BTC/USD Oracle: ${price['price_usd']:,.2f} (更新時間: {price['updated_at_dt']})")
        await oracle.close()

    asyncio.run(test())
