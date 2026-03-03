"""
ML 模型訓練 Pipeline - Phase 2/3
使用收集到的 Tick 數據訓練 LightGBM 分類器
並用 Isotonic Regression 做概率校準

Label 優先級:
  1. Oracle Label (data/processed/labeled_ticks.csv) - Phase 3 精確標簽
     使用 Chainlink Oracle(t+5min) vs Oracle(t) 比較
  2. Binance Label (data/raw/ticks_*.csv) - Fallback
     用 Binance 未來現貨價，有系統偏差，建議僅用於測試
"""
import glob
import pickle
from pathlib import Path
from typing import List, Optional

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
    Label 優先使用 Oracle Label (Phase 3)，Binance Label 作為 Fallback
    """

    def __init__(
        self,
        data_dir: str = "data/raw",
        processed_dir: str = "data/processed",
        model_dir: str = "models",
        forward_window_ms: int = 300_000,
        min_edge: float = 0.02
    ):
        self.data_dir = Path(data_dir)
        self.processed_dir = Path(processed_dir)
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.forward_window_ms = forward_window_ms
        self.min_edge = min_edge
        self.model = None
        self.label_source: Optional[str] = None  # 'oracle' | 'binance_fallback'

    # ------------------------------------------------------------------
    # FIX: 數據加載 — 自動選擇最精確的 Label 來源
    # ------------------------------------------------------------------

    def load_data(self) -> pd.DataFrame:
        """
        優先加載 Oracle Label，Fallback 到 Binance Label

        Oracle Label 為什麼更好:
        - Polymarket BTC 5m 市場結算基準是 Chainlink Oracle 價格
        - Binance 現貨價 vs Oracle 通常有 0.05-0.3% 差距
        - 在 5min 小幅波動市場，這個偏差直接導致 Label 噪訊和系統性詭算失賽
        """
        oracle_path = self.processed_dir / "labeled_ticks.csv"

        if oracle_path.exists():
            df = pd.read_csv(oracle_path)
            df = df.sort_values('timestamp').reset_index(drop=True)

            if 'oracle_label' not in df.columns:
                raise ValueError(
                    f"Oracle Label 文件缺少 oracle_label 欄位: {oracle_path}\n"
                    f"請重新執行 ./scripts/generate_oracle_labels.sh"
                )

            df['label'] = df['oracle_label']
            self.label_source = "oracle"
            logger.info(f"✅ 使用 Oracle Label ({len(df):,} 條) | 來源: {oracle_path}")
            logger.info(f"   Label 分佈: UP={df['label'].mean():.3f}, DOWN={(1-df['label'].mean()):.3f}")
            return df

        logger.warning("⚠️  Oracle Label 不存在，使用 Binance Label (有系統偏差)")
        logger.warning("   建議先執行: ./scripts/generate_oracle_labels.sh")
        df = self.load_ticks()
        df = self.generate_binance_labels(df)
        self.label_source = "binance_fallback"
        return df

    def load_ticks(self) -> pd.DataFrame:
        """\u52a0\u8f09\u6240\u6709 CSV Tick \u6578\u64da"""
        csv_files = glob.glob(str(self.data_dir / "ticks_*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"\u5728 {self.data_dir} \u627e\u4e0d\u5230 Tick \u6578\u64da")
        dfs = [pd.read_csv(f) for f in sorted(csv_files)]
        df = pd.concat(dfs, ignore_index=True)
        df = df.sort_values('timestamp').reset_index(drop=True)
        logger.info(f"\u52a0\u8f09 {len(df):,} \u689d Tick \u6578\u64da")
        return df

    # ------------------------------------------------------------------
    # Binance Label 生成（Fallback，有偏差警告）
    # ------------------------------------------------------------------

    def generate_binance_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        生成未來 5 分鐘 Binance 價格方向 Label (Fallback)
        注意: 此 Label 與 Polymarket Chainlink 結算有系統偏差
        """
        df = df.copy()
        future_prices = []
        prices = df['binance_price'].values
        timestamps = df['timestamp'].values

        for ts in timestamps:
            target_ts = ts + self.forward_window_ms
            future_idx = np.searchsorted(timestamps, target_ts)
            if future_idx >= len(prices):
                future_prices.append(np.nan)
            else:
                future_prices.append(prices[future_idx])

        df['future_price'] = future_prices
        df['label'] = (df['future_price'] > df['binance_price']).astype(int)
        df = df.dropna(subset=['future_price'])
        logger.info(f"Binance Label 分佈: UP={df['label'].mean():.3f}, DOWN={(1-df['label'].mean()):.3f}")
        return df

    # ------------------------------------------------------------------
    # 模型訓練
    # ------------------------------------------------------------------

    def train(self) -> dict:
        """完整訓練 Pipeline，返回評估指標"""
        df = self.load_data()

        available_features = [col for col in FEATURE_COLS if col in df.columns]
        missing = [col for col in FEATURE_COLS if col not in df.columns]
        if missing:
            logger.warning(f"缺少特徵欄位 (將用 0 填充): {missing}")

        X = df[available_features].fillna(0).values
        y = df['label'].values

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
        calibrated = CalibratedClassifierCV(
            estimator=base_model,
            method='isotonic',
            cv=tscv
        )
        calibrated.fit(X, y)
        self.model = calibrated

        train_idx, val_idx = list(tscv.split(X))[-1]
        X_val, y_val = X[val_idx], y[val_idx]
        probs = calibrated.predict_proba(X_val)[:, 1]

        metrics = {
            'log_loss': log_loss(y_val, probs),
            'brier_score': brier_score_loss(y_val, probs),
            'n_train': len(train_idx),
            'n_val': len(val_idx),
            'label_source': self.label_source,
            'features_used': available_features
        }

        logger.info(
            f"訓練完成 | LabelSource={self.label_source} | "
            f"LogLoss={metrics['log_loss']:.4f} | Brier={metrics['brier_score']:.4f}"
        )
        self.save_model(available_features)
        return metrics

    # ------------------------------------------------------------------
    # 模型存取
    # ------------------------------------------------------------------

    def save_model(self, feature_cols: List[str] = None):
        """保存訓練好的模型"""
        path = self.model_dir / "calibrated_lgb.pkl"
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'feature_cols': feature_cols or FEATURE_COLS,
                'label_source': self.label_source
            }, f)
        logger.info(f"模型已保存: {path} (label_source={self.label_source})")

    @classmethod
    def load_model(cls, model_dir: str = "models"):
        """加載已訓練模型，返回 (model, feature_cols)"""
        path = Path(model_dir) / "calibrated_lgb.pkl"
        if not path.exists():
            raise FileNotFoundError(f"找不到模型文件: {path}")

        with open(path, 'rb') as f:
            data = pickle.load(f)

        label_src = data.get('label_source', 'unknown')
        if label_src == 'binance_fallback':
            logger.warning(
                f"⚠️  已加載模型使用 Binance Label 訓練，建議用 Oracle Label 重新訓練\n"
                f"   執行: ./scripts/generate_oracle_labels.sh && ./scripts/train_model.sh"
            )
        else:
            logger.info(f"✅ 模型已加載: {path} (label_source={label_src})")

        return data['model'], data['feature_cols']


if __name__ == "__main__":
    trainer = ModelTrainer()
    metrics = trainer.train()
    print(f"\n訓練結果: {metrics}")
