"""
ML 模型推理引擎 - Phase 2.4
加載已訓練模型，對實時特徵向量進行推理
"""
import numpy as np
from typing import Optional, Tuple
from pathlib import Path

from src.utils.logger import logger


class ModelInference:
    """
    實時推理引擎
    輸入: 特徵字典 (來自 FeatureCalculator)
    輸出: 上漲概率 (0~1)
    """

    def __init__(self, model_dir: str = "models"):
        self.model_dir = Path(model_dir)
        self.model = None
        self.feature_cols = None
        self.is_loaded = False

    def load(self) -> bool:
        """加載模型，返回是否成功"""
        try:
            from src.strategy_engine.model_trainer import ModelTrainer
            self.model, self.feature_cols = ModelTrainer.load_model(str(self.model_dir))
            self.is_loaded = True
            logger.info(f"✅ ML 模型已加載，特徵數: {len(self.feature_cols)}")
            return True
        except FileNotFoundError:
            logger.warning("⚠️  找不到訓練好的模型，將使用 Phase 1 規則信號")
            self.is_loaded = False
            return False

    def predict_proba(self, features: dict) -> Tuple[float, bool]:
        """
        預測上漲概率

        Args:
            features: FeatureCalculator.compute_features() 的輸出

        Returns:
            (prob_up, is_valid): 上漲概率 + 是否有效 (模型已加載)
        """
        if not self.is_loaded:
            return 0.5, False

        try:
            X = np.array([[features.get(col, 0.0) for col in self.feature_cols]])
            prob = float(self.model.predict_proba(X)[0, 1])
            return prob, True
        except Exception as e:
            logger.error(f"模型推理失敗: {e}")
            return 0.5, False

    def get_edge(self, features: dict, market_price: float) -> Optional[float]:
        """
        計算信號邊際優勢
        edge = predicted_prob - market_price

        Args:
            features: 特徵字典
            market_price: Polymarket 當前報價 (0~1)

        Returns:
            edge 值，若模型未加載返回 None
        """
        prob, valid = self.predict_proba(features)
        if not valid:
            return None
        return prob - market_price
