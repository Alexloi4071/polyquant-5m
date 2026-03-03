"""
簽名修復驗證測試
測試新的 OrderExecutor 是否能正確初始化 SDK 並處理簽名邏輯
"""
import os
import sys
import asyncio
from pathlib import Path

# 將 src 加入路徑
sys.path.append(str(Path(__file__).parent.parent))

from src.execution_engine.order_executor import OrderExecutor
from src.execution_engine.inventory_manager import InventoryManager

def test_initialization():
    """測試初始化是否正常 (不依賴真實私鑰)"""
    print("--- 測試 OrderExecutor 初始化 ---")
    
    # 模擬數據
    dummy_pk = "0x" + "1" * 64
    dummy_token = "0x" + "2" * 64
    inv_mgr = InventoryManager()
    
    try:
        executor = OrderExecutor(
            private_key=dummy_pk,
            token_id=dummy_token,
            inventory_mgr=inv_mgr,
            trading_enabled=False # 紙盤模式
        )
        print("✅ 初始化成功")
        
        # 測試紙盤下單
        print("--- 測試紙盤下單 (Paper Trading) ---")
        
        async def run_paper_test():
            res = await executor.place_fok_order(
                side="BUY",
                price=0.5,
                size=10.0,
                token_side="YES",
                strategy="TEST"
            )
            if res and "paper" in res['status']:
                print(f"✅ 紙盤下單邏輯正常: {res}")
            else:
                print(f"❌ 紙盤下單邏輯異常: {res}")

        asyncio.run(run_paper_test())
        
    except Exception as e:
        print(f"❌ 初始化失敗: {e}")

if __name__ == "__main__":
    test_initialization()
