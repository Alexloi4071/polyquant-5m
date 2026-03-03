"""
實時特徵計算引擎
從 Binance 原始數據流構建 ML 推理用的特徵向量

特徵列表:
  - obi_5s, obi_30s, obi_1m: 多窗口訂單簿失衡
  - cvd_30s, cvd_1m: 累計成交量差
  - taker_buy_ratio_30s, taker_buy_ratio_1m: 主動買入比例
  - price_return_5s, price_return_30s: 價格收益率
  - volatility_1m: 1分鐘滾動波動率
  - whale_count_1m: 1分鐘內大單次數
"""
import time
from collections import deque
from typing import Dict, List, Optional
import numpy as np

from src.data_engine.binance_stream import BinanceStream
from src.utils.helpers import timestamp_ms


class FeatureCalculator:
    """實時特徵計算器，用於 ML 模型推理"""

    def __init__(self, binance_stream: BinanceStream):
        self.binance = binance_stream
        
        # 時間窗口緩衝區
        self.price_history = deque(maxlen=3000)  # 5分鐘歷史
        self.obi_history = deque(maxlen=1200)
        self.whale_events = deque(maxlen=100)
    
    def compute_features(self) -> Dict[str, float]:
        """
        計算當前時刻的完整特徵向量
        
        Returns:
            feature_dict: {feature_name: value}
        """
        now_ts = timestamp_ms()
        
        # 更新內部緩衝
        self._update_buffers(now_ts)
        
        features = {}
        
        # === 訂單簿失衡 (OBI) 多窗口 ===
        features['obi_5s'] = self._windowed_obi(5000, now_ts)
        features['obi_30s'] = self._windowed_obi(30000, now_ts)
        features['obi_1m'] = self._windowed_obi(60000, now_ts)
        
        # === 成交量特徵 ===
        flow_30s = self._windowed_flow(30000, now_ts)
        flow_1m = self._windowed_flow(60000, now_ts)
        
        features['cvd_30s'] = flow_30s['delta']
        features['cvd_1m'] = flow_1m['delta']
        features['taker_buy_ratio_30s'] = flow_30s['buy_ratio']
        features['taker_buy_ratio_1m'] = flow_1m['buy_ratio']
        
        # === 價格特徵 ===
        features['price_return_5s'] = self._price_return(5000, now_ts)
        features['price_return_30s'] = self._price_return(30000, now_ts)
        features['volatility_1m'] = self._realized_volatility(60000, now_ts)
        
        # === 大單特徵 ===
        features['whale_count_1m'] = self._count_whales(60000, now_ts)
        
        # === 當前價格 (用於歸一化) ===
        features['current_price'] = self.binance.last_price
        
        return features
    
    def _update_buffers(self, now_ts: int):
        """更新內部時間序列緩衝"""
        if self.binance.last_price > 0:
            self.price_history.append({'price': self.binance.last_price, 'ts': now_ts})
        
        obi = self.binance.get_obi()
        self.obi_history.append({'obi': obi, 'ts': now_ts})
    
    def _windowed_obi(self, window_ms: int, now_ts: int) -> float:
        """計算指定窗口內的平均 OBI"""
        cutoff = now_ts - window_ms
        valid = [x['obi'] for x in self.obi_history if x['ts'] >= cutoff]
        return float(np.mean(valid)) if valid else 0.0
    
    def _windowed_flow(self, window_ms: int, now_ts: int) -> Dict:
        """計算指定窗口內的成交量流"""
        cutoff_ts = now_ts - window_ms
        trades = [t for t in self.binance.recent_trades if t['ts'] >= cutoff_ts]
        
        buy_vol = sum(t['qty'] for t in trades if not t['is_buyer_maker'])
        sell_vol = sum(t['qty'] for t in trades if t['is_buyer_maker'])
        
        return {
            'delta': buy_vol - sell_vol,
            'buy_ratio': buy_vol / (buy_vol + sell_vol + 1e-9)
        }
    
    def _price_return(self, window_ms: int, now_ts: int) -> float:
        """計算窗口內的價格收益率"""
        cutoff = now_ts - window_ms
        prices = [x['price'] for x in self.price_history if x['ts'] >= cutoff]
        
        if len(prices) < 2:
            return 0.0
        
        return (prices[-1] - prices[0]) / (prices[0] + 1e-9)
    
    def _realized_volatility(self, window_ms: int, now_ts: int) -> float:
        """計算實現波動率"""
        cutoff = now_ts - window_ms
        prices = [x['price'] for x in self.price_history if x['ts'] >= cutoff]
        
        if len(prices) < 10:
            return 0.0
        
        returns = np.diff(prices) / (np.array(prices[:-1]) + 1e-9)
        return float(np.std(returns))
    
    def _count_whales(self, window_ms: int, now_ts: int) -> int:
        """統計窗口內大單次數"""
        cutoff_ts = now_ts - window_ms
        threshold = 10.0
        
        count = sum(
            1 for t in self.binance.recent_trades 
            if t['ts'] >= cutoff_ts and t['qty'] >= threshold
        )
        return count
    
    def get_feature_names(self) -> List[str]:
        """返回特徵名稱列表 (用於模型訓練時對齊)"""
        return [
            'obi_5s', 'obi_30s', 'obi_1m',
            'cvd_30s', 'cvd_1m',
            'taker_buy_ratio_30s', 'taker_buy_ratio_1m',
            'price_return_5s', 'price_return_30s',
            'volatility_1m',
            'whale_count_1m'
        ]
