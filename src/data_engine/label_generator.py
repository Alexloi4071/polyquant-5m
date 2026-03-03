"""
Oracle Label 生成器 - Phase 3
使用 Chainlink Oracle 結算價格生成訓練 Label
解決 Binance 現貨價格與 Polymarket 結算價格不一致的問題
"""
import asyncio
import glob
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.data_engine.chainlink_oracle import ChainlinkOracle
from src.utils.logger import logger


class OracleLabelGenerator:
    """
    Phase 3: 使用 Chainlink Oracle 價格替換 Binance 現貨價格生成 Label
    
    為什麼需要 Oracle Label:
    - Polymarket BTC 5m 市場結算基準是 Chainlink Oracle 價格
    - Binance 現貨和 Oracle 之間有微小但重要的價差
    - 用 Oracle Label 訓練可以避免系統性偏差
    """

    def __init__(
        self,
        data_dir: str = "data/raw",
        output_dir: str = "data/processed",
        forward_window_s: int = 300,   # 5 分鐘
        tolerance_s: int = 30          # Oracle 時間對齊容忍度
    ):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.forward_window_s = forward_window_s
        self.tolerance_s = tolerance_s
        self.oracle = ChainlinkOracle()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def generate(self, max_rows: Optional[int] = None) -> pd.DataFrame:
        """
        完整 Label 生成 Pipeline:
        1. 加載 Tick 數據
        2. 採樣關鍵時間點
        3. 查詢 Chainlink Oracle 價格
        4. 生成方向 Label
        5. 保存到 data/processed/
        """
        df = self._load_ticks(max_rows)
        df = self._downsample(df)  # 每 5 秒採樣一次，降低 Oracle 查詢次數

        logger.info(f"開始查詢 Chainlink Oracle，共 {len(df)} 個時間點...")
        oracle_prices = await self._fetch_oracle_prices(df)
        df['oracle_price'] = oracle_prices

        df = df.dropna(subset=['oracle_price'])
        df = self._generate_labels(df)

        output_path = self.output_dir / "labeled_ticks.csv"
        df.to_csv(output_path, index=False)
        logger.info(f"已保存 {len(df):,} 條帶 Oracle Label 的數據: {output_path}")

        await self.oracle.close()
        return df

    # ------------------------------------------------------------------
    # 輔助方法
    # ------------------------------------------------------------------

    def _load_ticks(self, max_rows: Optional[int]) -> pd.DataFrame:
        """加載原始 Tick 數據"""
        csv_files = glob.glob(str(self.data_dir / "ticks_*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"找不到 Tick 數據: {self.data_dir}")

        dfs = [pd.read_csv(f) for f in sorted(csv_files)]
        df = pd.concat(dfs, ignore_index=True)
        df = df.sort_values('timestamp').reset_index(drop=True)

        if max_rows:
            df = df.head(max_rows)

        logger.info(f"加載 {len(df):,} 條 Tick 數據")
        return df

    def _downsample(self, df: pd.DataFrame, interval_ms: int = 5000) -> pd.DataFrame:
        """按時間間隔降採樣，減少 Oracle 查詢次數"""
        df['time_bucket'] = (df['timestamp'] // interval_ms) * interval_ms
        df = df.groupby('time_bucket').last().reset_index()
        df = df.rename(columns={'time_bucket': 'timestamp'})
        logger.info(f"降採樣後: {len(df):,} 個數據點")
        return df

    async def _fetch_oracle_prices(self, df: pd.DataFrame) -> list:
        """批量查詢 Oracle 價格（帶並發限制）"""
        oracle_prices = []
        semaphore = asyncio.Semaphore(3)  # 最多 3 個並發請求

        async def fetch_one(ts_ms: int) -> Optional[float]:
            async with semaphore:
                ts_s = ts_ms // 1000 + self.forward_window_s
                price = await self.oracle.get_price_at_timestamp(ts_s, self.tolerance_s)
                await asyncio.sleep(0.1)  # 避免 RPC 限流
                return price

        tasks = [fetch_one(int(row['timestamp'])) for _, row in df.iterrows()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                oracle_prices.append(None)
            else:
                oracle_prices.append(r)

        success = sum(1 for p in oracle_prices if p is not None)
        logger.info(f"Oracle 查詢完成: {success}/{len(oracle_prices)} 成功")
        return oracle_prices

    def _generate_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        生成 Oracle Label
        Y=1: Oracle 結算價 > 當前 Binance 現貨價 (Polymarket YES 勝出)
        Y=0: Oracle 結算價 <= 當前 Binance 現貨價
        """
        df = df.copy()
        df['oracle_label'] = (df['oracle_price'] > df['binance_price']).astype(int)

        up_pct = df['oracle_label'].mean()
        logger.info(
            f"Oracle Label 分佈: UP={up_pct:.3f} "
            f"DOWN={(1-up_pct):.3f} "
            f"(總樣本: {len(df):,})"
        )
        return df


if __name__ == "__main__":
    async def run():
        gen = OracleLabelGenerator()
        df = await gen.generate()
        print(df[['timestamp', 'binance_price', 'oracle_price', 'oracle_label']].head(10))

    asyncio.run(run())
