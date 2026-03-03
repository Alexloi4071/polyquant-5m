"""
連線測試腳本
部署後執行: python tests/test_connections.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
import aiohttp

async def test_binance_ws():
    """測試 Binance WebSocket 連接"""
    url = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    try:
        async with websockets.connect(url) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"✅ Binance WS: OK (收到 {len(msg)} bytes)")
    except Exception as e:
        print(f"❌ Binance WS: FAILED - {e}")

async def test_polymarket_api():
    """測試 Polymarket API 連接"""
    url = "https://clob.polymarket.com/markets?next_cursor="
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    print(f"✅ Polymarket API: OK (status={resp.status})")
                else:
                    print(f"⚠️  Polymarket API: status={resp.status}")
    except Exception as e:
        print(f"❌ Polymarket API: FAILED - {e}")

async def main():
    print("\n=== PolyQuant-5m 連線測試 ===")
    await test_binance_ws()
    await test_polymarket_api()
    print("========================\n")

if __name__ == "__main__":
    asyncio.run(main())
