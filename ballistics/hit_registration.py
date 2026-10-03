"""
命中判定与受击盒系统 - 核心模块（带bug版本）

已知问题：
- 受击盒只有球形不区分部位不考虑姿态
- 伤害区域完全没做无优先级无倍率
- 延迟补偿用服务器当前位置无上限
- 命中反馈只有准星变色无声音无部位
- 弹道是直线不考虑飞行时间重力穿透
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
import time


@dataclass
class Config:
    """配置项"""
    pass


class HitRegistration:
    """命中判定与受击盒系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history = []

    def update(self, dt: float):
        """每帧更新"""
        pass

    def reset(self):
        """重置状态"""
        self._state = {}
        self._history = []
