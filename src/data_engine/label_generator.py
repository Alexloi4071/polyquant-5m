"""
Oracle Label 生成器 - Phase 3
使用 Chainlink Oracle 結算價格生成訓練 Label

FIX: 使用 Oracle(t+5min) vs Oracle(t) 比較，消除 Binance 偏差
原啷代碼錯誤: 使用 Oracle(t+5min) > Binance(t)，存在系統性偏差
在 5min 小幅波動市場，Chainlink vs Binance 的 0.05-0.3% 差距會繫致 Label 噪訊
"""
import asyncio
import glob
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from src.data_engine.chainlink_oracle import ChainlinkOracle
from src.utils.logger import logger


class OracleLabelGenerator:
    """
    Phase 3: 使用 Chainlink Oracle 價格生成精確 Label

    FIX: Oracle(t+5min) > Oracle(t) => Y=1
    原始代碼錯誤: 使用 Oracle(t+5min) > Binance(t)，存在系統性偏差
    """

    def __init__(
        self,
        data_dir: str = "data/raw",
        output_dir: str = "data/processed",
        forward_window_s: int = 300,
        tolerance_s: int = 30
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
        2. 檡樣關鍵時間點
        3. FIX: 並行查詢 Oracle(t) 和 Oracle(t+5min) 兩個時間點
        4. Oracle(t+5min) vs Oracle(t) 生成方向 Label
        5. 保存到 data/processed/
        """
        df = self._load_ticks(max_rows)
        df = self._downsample(df)

        logger.info(f"開始查詢 Chainlink Oracle，共 {len(df)} 個時間點 (每點查詢 2 次)...")
        current_prices, future_prices = await self._fetch_oracle_price_pairs(df)

        df['oracle_price_current'] = current_prices
        df['oracle_price_future'] = future_prices
        df = df.dropna(subset=['oracle_price_current', 'oracle_price_future'])

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
        """\u52a0\u8f09\u539f\u59cb Tick \u6578\u64da"""
        csv_files = glob.glob(str(self.data_dir / "ticks_*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"\u627e\u4e0d\u5230 Tick \u6578\u64da: {self.data_dir}")
        dfs = [pd.read_csv(f) for f in sorted(csv_files)]
        df = pd.concat(dfs, ignore_index=True)
        df = df.sort_values('timestamp').reset_index(drop=True)
        if max_rows:
            df = df.head(max_rows)
        logger.info(f"\u52a0\u8f09 {len(df):,} \u689d Tick \u6578\u64da")
        return df

    def _downsample(self, df: pd.DataFrame, interval_ms: int = 5000) -> pd.DataFrame:
        """\u6309\u6642\u9593\u9593\u9694\u964d\u63a1\u6a23\uff0c\u6e1b\u5c11 Oracle \u67e5\u8a62\u6b21\u6578"""
        df['time_bucket'] = (df['timestamp'] // interval_ms) * interval_ms
        df = df.groupby('time_bucket').last().reset_index()
        df = df.rename(columns={'time_bucket': 'timestamp'})
        logger.info(f"\u964d\u63a1\u6a23\u5f8c: {len(df):,} \u500b\u6578\u64da\u9ede")
        return df

    async def _fetch_oracle_price_pairs(self, df: pd.DataFrame) -> Tuple[list, list]:
        """
        FIX: 批量查詢 Oracle 價格對 (Oracle(t) + Oracle(t+5min))

        原始代碼只查詢 Oracle(t+5min)，與 Binance(t) 比較存在偏差
        修定為同時查詢兩個 Oracle 時間點，完全消除 Binance 引入的誤差
        """
        semaphore = asyncio.Semaphore(3)

        async def fetch_pair(ts_ms: int) -> Tuple[Optional[float], Optional[float]]:
            async with semaphore:
                ts_s = ts_ms // 1000
                future_ts_s = ts_s + self.forward_window_s
                # 並行查詢兩個時間點，降低總耗時
                current_price, future_price = await asyncio.gather(
                    self.oracle.get_price_at_timestamp(ts_s, self.tolerance_s),
                    self.oracle.get_price_at_timestamp(future_ts_s, self.tolerance_s)
                )
                await asyncio.sleep(0.1)  # 避免 RPC 限流
                return current_price, future_price

        tasks = [fetch_pair(int(row['timestamp'])) for _, row in df.iterrows()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        current_prices: List[Optional[float]] = []
        future_prices: List[Optional[float]] = []
        success = 0

        for r in results:
            if isinstance(r, Exception) or r[0] is None or r[1] is None:
                current_prices.append(None)
                future_prices.append(None)
            else:
                current_prices.append(r[0])
                future_prices.append(r[1])
                success += 1

        logger.info(f"Oracle 查詢完成: {success}/{len(results)} 成功")
        return current_prices, future_prices

    def _generate_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        FIX: 生成 Oracle Label (Oracle-vs-Oracle 比較)

        Y=1: Oracle(t+5min) > Oracle(t)  ← 正確
        原始代碼: Oracle(t+5min) > Binance(t)  ← 有系統偏差

        為什麼重要:
        Chainlink Oracle vs Binance 現貨通常有 0.05-0.3% 差距
        在 5min 小幅波動市場中，這個偏差會造成 Label 噪訊和系統性計算失賽
        """
        df = df.copy()
        df['oracle_label'] = (
            df['oracle_price_future'] > df['oracle_price_current']
        ).astype(int)

        up_pct = df['oracle_label'].mean()
        avg_move = (df['oracle_price_future'] - df['oracle_price_current']).mean()
        logger.info(
            f"Oracle Label 分佈: UP={up_pct:.3f} DOWN={(1-up_pct):.3f} "
            f"(總樣本: {len(df):,}) | Oracle 平均價差: ${avg_move:.2f}"
        )
        return df


if __name__ == "__main__":
    async def run():
        gen = OracleLabelGenerator()
        df = await gen.generate()
        print(df[['timestamp', 'oracle_price_current', 'oracle_price_future', 'oracle_label']].head(10))

    asyncio.run(run())
