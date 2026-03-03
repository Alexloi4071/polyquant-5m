"""
ML 模型訓練 Pipeline - Phase 2.3
使用收集到的 Tick 數據訓練 LightGBM 分類器
並用 Isotonic Regression 做概率校準
"""
import os
import glob
import pickle
from pathlib import Path
from typing import Tuple, List

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import log_loss, brier_score_loss

from src.utils.logger import logger

FEATURE_COLS = [
    'obi_5s', 'obi_30s', 'obi_1m',
    'cvd_30s', 'cvd_1m',
    'taker_buy_ratio_30s', 'taker_buy_ratio_1m',
    'price_return_5s', 'price_return_30s',
    'volatility_1m', 'whale_count_1m'
]


class ModelTrainer:
    """
    LightGBM + Isotonic Calibration 訓練器
    Label Y: 未來 5 分鐘 Binance 收盤價 > 當前價格 => 1, 否則 0
    """

    def __init__(
        self,
        data_dir: str = "data/raw",
        model_dir: str = "models",
        forward_window_ms: int = 300_000,  # 5分鐘
        min_edge: float = 0.02             # 最小邊際概率優勢
    ):
        self.data_dir = Path(data_dir)
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.forward_window_ms = forward_window_ms
        self.min_edge = min_edge
        self.model = None

    # ------------------------------------------------------------------
    # 數據加載
    # ------------------------------------------------------------------

    def load_ticks(self) -> pd.DataFrame:
        """加載所有 CSV Tick 數據"""
        csv_files = glob.glob(str(self.data_dir / "ticks_*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"在 {self.data_dir} 找不到 Tick 數據")

        dfs = [pd.read_csv(f) for f in sorted(csv_files)]
        df = pd.concat(dfs, ignore_index=True)
        df = df.sort_values('timestamp').reset_index(drop=True)
        logger.info(f"加載 {len(df):,} 條 Tick 數據")
        return df

    # ------------------------------------------------------------------
    # Label 生成 (Binance 價格 Label)
    # ------------------------------------------------------------------

    def generate_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        生成未來 5 分鐘價格方向 Label
        Y=1: 未來 5 分鐘後價格上漲
        Y=0: 未來 5 分鐘後價格下跌
        """
        df = df.copy()
        future_prices = []

        prices = df['binance_price'].values
        timestamps = df['timestamp'].values

        for i, ts in enumerate(timestamps):
            target_ts = ts + self.forward_window_ms
            # 找到最接近目標時間戳的價格
            future_idx = np.searchsorted(timestamps, target_ts)
            if future_idx >= len(prices):
                future_prices.append(np.nan)
            else:
                future_prices.append(prices[future_idx])

        df['future_price'] = future_prices
        df['label'] = (df['future_price'] > df['binance_price']).astype(int)
        df = df.dropna(subset=['future_price'])
        logger.info(f"Label 分佈: UP={df['label'].mean():.3f}, DOWN={(1-df['label'].mean()):.3f}")
        return df

    # ------------------------------------------------------------------
    # 模型訓練
    # ------------------------------------------------------------------

    def train(self) -> dict:
        """完整訓練 Pipeline，返回評估指標"""
        df = self.load_ticks()
        df = self.generate_labels(df)

        X = df[FEATURE_COLS].fillna(0).values
        y = df['label'].values

        # 時間序列交叉驗證 (不能用隨機 shuffle)
        tscv = TimeSeriesSplit(n_splits=5)

        lgb_params = {
            'objective': 'binary',
            'metric': 'binary_logloss',
            'learning_rate': 0.05,
            'num_leaves': 31,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'min_child_samples': 100,
            'verbose': -1,
            'n_estimators': 300
        }

        base_model = lgb.LGBMClassifier(**lgb_params)

        # Isotonic Calibration 校準概率
        calibrated = CalibratedClassifierCV(
            estimator=base_model,
            method='isotonic',
            cv=tscv
        )
        calibrated.fit(X, y)

        self.model = calibrated

        # 評估最後一折
        train_idx, val_idx = list(tscv.split(X))[-1]
        X_val, y_val = X[val_idx], y[val_idx]
        probs = calibrated.predict_proba(X_val)[:, 1]

        metrics = {
            'log_loss': log_loss(y_val, probs),
            'brier_score': brier_score_loss(y_val, probs),
            'n_train': len(train_idx),
            'n_val': len(val_idx)
        }

        logger.info(f"訓練完成 | LogLoss={metrics['log_loss']:.4f} | Brier={metrics['brier_score']:.4f}")

        self.save_model()
        return metrics

    # ------------------------------------------------------------------
    # 模型存取
    # ------------------------------------------------------------------

    def save_model(self):
        """保存訓練好的模型"""
        path = self.model_dir / "calibrated_lgb.pkl"
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'feature_cols': FEATURE_COLS
            }, f)
        logger.info(f"模型已保存: {path}")

    @classmethod
    def load_model(cls, model_dir: str = "models"):
        """加載已訓練模型，返回 (model, feature_cols)"""
        path = Path(model_dir) / "calibrated_lgb.pkl"
        if not path.exists():
            raise FileNotFoundError(f"找不到模型文件: {path}")

        with open(path, 'rb') as f:
            data = pickle.load(f)

        logger.info(f"模型已加載: {path}")
        return data['model'], data['feature_cols']


if __name__ == "__main__":
    trainer = ModelTrainer()
    metrics = trainer.train()
    print(f"\n訓練結果: {metrics}")
