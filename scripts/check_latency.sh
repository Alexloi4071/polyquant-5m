#!/bin/bash
# =====================================================
# 延遲測試腳本
# 部署後第一件事: 跑這個確認你的 VM 延遲合格
# =====================================================

echo "=== Polymarket 伺服器延遲測試 ==="
echo "目標: 延遲應 < 50ms (倫敦 VM 預期 1-5ms)"
echo ""

echo "[1] Polymarket CLOB API:"
ping -c 10 clob.polymarket.com | tail -1

echo ""
echo "[2] Binance WebSocket:"
ping -c 10 stream.binance.com | tail -1

echo ""
echo "[3] Polygon RPC:"
ping -c 5 polygon-rpc.com | tail -1

echo ""
echo "如果延遲 > 100ms，請確認你的 VM 在 europe-west2 (倫敦) 區域"
