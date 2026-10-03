"""
命中判定与受击盒系统 - 核心模块

功能：
- check_hitbox(): 分部位（头/胸/腿）且随姿态（站/蹲/跳）变化的受击盒
- hit_zone_damage(): 伤害区域按优先级选取并应用部位倍率（爆头 2 倍）
- lag_compensation(): 回滚到客户端开枪时刻的位置，有延迟上限与合理性校验
- hit_feedback(): 显示命中部位/伤害数值，有声音反馈并区分致命命中
- projectile_tracers(): 考虑飞行时间与重力的抛物弹道，支持穿透分段显示
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
import math


# 部位定义：priority 越大越优先；multiplier 为伤害倍率
HIT_ZONES: Dict[str, Dict[str, float]] = {
    "head": {"priority": 3, "multiplier": 2.0},
    "chest": {"priority": 2, "multiplier": 1.0},
    "legs": {"priority": 1, "multiplier": 0.75},
}

# 不同姿态下的受击盒尺寸（米）。target_position 为脚底位置，y 轴朝上。
# 站立/跳跃为全尺寸；蹲下高度明显变小。
POSTURE_HITBOXES: Dict[str, Dict[str, float]] = {
    "standing": {"height": 1.8, "radius": 0.4},
    "jumping": {"height": 1.8, "radius": 0.4},
    "crouching": {"height": 1.1, "radius": 0.4},
}

# 部位在受击盒高度上的分界（从脚底算起的比例）
_HEAD_FRACTION = 0.85
_CHEST_FRACTION = 0.45

# 延迟补偿上限（毫秒），超过部分不补偿
DEFAULT_MAX_LATENCY_MS = 200.0
# 客户端上报位置与服务器当前位置允许的最大偏差（米），超出视为伪造延迟
DEFAULT_MAX_POSITION_DEVIATION = 5.0

# 弹道最短显示时间（秒），保证玩家能看到
DEFAULT_MIN_TRACER_DISPLAY_TIME = 0.5
DEFAULT_GRAVITY = 9.8


@dataclass
class Config:
    """配置项"""
    max_latency_ms: float = DEFAULT_MAX_LATENCY_MS
    max_position_deviation: float = DEFAULT_MAX_POSITION_DEVIATION
    min_tracer_display_time: float = DEFAULT_MIN_TRACER_DISPLAY_TIME
    gravity: float = DEFAULT_GRAVITY


def _distance2d(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _distance3d(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


class HitRegistration:
    """命中判定与受击盒系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state: Dict = {}
        self._history: List = []
        self.max_latency_ms = float(
            self.config.get("max_latency_ms", DEFAULT_MAX_LATENCY_MS)
        )
        self.max_position_deviation = float(
            self.config.get(
                "max_position_deviation", DEFAULT_MAX_POSITION_DEVIATION
            )
        )
        self.min_tracer_display_time = float(
            self.config.get(
                "min_tracer_display_time", DEFAULT_MIN_TRACER_DISPLAY_TIME
            )
        )
        self.gravity = float(self.config.get("gravity", DEFAULT_GRAVITY))

    def update(self, dt: float):
        """每帧更新"""
        pass

    def reset(self):
        """重置状态"""
        self._state = {}
        self._history = []

    def check_hitbox(
        self,
        shot_point: Sequence[float],
        target_position: Sequence[float],
        posture: str = "standing",
    ) -> Dict:
        """检测子弹经过的点是否命中目标受击盒，并判定部位。

        :param shot_point: 子弹经过点 (x, y, z)，y 轴朝上
        :param target_position: 目标脚底位置 (x, y, z)
        :param posture: 目标姿态 standing/crouching/jumping
        :return: {"hit": bool, "zone": str|None, "posture": str,
                  "height": float, "radius": float}
        """
        box = POSTURE_HITBOXES.get(posture)
        if box is None:
            raise ValueError(f"未知姿态: {posture}")

        height = box["height"]
        radius = box["radius"]
        rel_y = shot_point[1] - target_position[1]
        horizontal_dist = _distance2d(shot_point, target_position)

        if horizontal_dist > radius or rel_y < 0.0 or rel_y > height:
            return {
                "hit": False,
                "zone": None,
                "posture": posture,
                "height": height,
                "radius": radius,
            }

        fraction = rel_y / height
        if fraction >= _HEAD_FRACTION:
            zone = "head"
        elif fraction >= _CHEST_FRACTION:
            zone = "chest"
        else:
            zone = "legs"

        return {
            "hit": True,
            "zone": zone,
            "posture": posture,
            "height": height,
            "radius": radius,
        }

    def hit_zone_damage(
        self, base_damage: float, zones_hit: Sequence[str]
    ) -> Dict:
        """根据子弹穿过的部位计算最终伤害。

        当子弹同时穿过多个部位时，按优先级（头 > 胸 > 腿）选取，
        并应用该部位的伤害倍率。

        :param base_damage: 基础伤害
        :param zones_hit: 子弹穿过的部位列表
        :return: {"zone": str|None, "damage": float, "multiplier": float}
        """
        if not zones_hit:
            return {"zone": None, "damage": 0.0, "multiplier": 0.0}

        zone = max(
            zones_hit, key=lambda z: HIT_ZONES.get(z, {"priority": 0})["priority"]
        )
        multiplier = HIT_ZONES[zone]["multiplier"]
        return {
            "zone": zone,
            "damage": base_damage * multiplier,
            "multiplier": multiplier,
        }

    def lag_compensation(
        self,
        client_position: Sequence[float],
        server_position: Sequence[float],
        latency_ms: float,
    ) -> Dict:
        """计算延迟补偿后用于命中判定的目标位置。

        - 使用客户端开枪时刻上报的位置，而非服务器当前位置；
        - 延迟超过上限时，只补偿到上限；
        - 负延迟或客户端位置与服务器位置偏差过大（疑似伪造延迟/瞬移）
          时，拒绝补偿并回退到服务器当前位置。

        :return: {"position": (x, y, z), "applied_latency_ms": float,
                  "valid": bool, "reason": str}
        """
        if latency_ms is None or latency_ms < 0:
            return {
                "position": tuple(server_position),
                "applied_latency_ms": 0.0,
                "valid": False,
                "reason": "非法延迟值",
            }

        deviation = _distance3d(client_position, server_position)
        if deviation > self.max_position_deviation:
            return {
                "position": tuple(server_position),
                "applied_latency_ms": 0.0,
                "valid": False,
                "reason": "位置偏差过大，疑似伪造延迟",
            }

        applied = min(float(latency_ms), self.max_latency_ms)
        return {
            "position": tuple(client_position),
            "applied_latency_ms": applied,
            "valid": True,
            "reason": "ok",
        }

    def hit_feedback(
        self,
        zone: str,
        damage: float,
        target_remaining_health: float,
    ) -> Dict:
        """生成命中反馈：准星变色 + 部位 + 伤害数值 + 声音 + 致命区分。"""
        lethal = damage >= target_remaining_health
        if lethal:
            sound = "kill"
            crosshair_color = "red"
        elif zone == "head":
            sound = "headshot"
            crosshair_color = "yellow"
        else:
            sound = "hit"
            crosshair_color = "white"

        return {
            "hit": True,
            "zone": zone,
            "zone_label": {"head": "头部", "chest": "胸部", "legs": "腿部"}.get(
                zone, zone
            ),
            "damage": damage,
            "lethal": lethal,
            "sound": sound,
            "crosshair_color": crosshair_color,
        }

    def projectile_tracers(
        self,
        origin: Sequence[float],
        direction: Sequence[float],
        speed: float,
        obstacles: Optional[Sequence[Dict]] = None,
        max_flight_time: float = 5.0,
        step: float = 0.005,
    ) -> Dict:
        """模拟考虑重力与飞行时间的弹道，并生成可穿透的分段弹道。

        :param origin: 枪口位置
        :param direction: 射击方向（单位向量无需，内部归一化）
        :param speed: 子弹初速度（米/秒）
        :param obstacles: 障碍物列表，每项为
            {"position": (x,y,z), "radius": r, "penetrable": bool}
        :return: {"segments": [{"start", "end", "penetrated"}],
                  "flight_time": float, "display_time": float,
                  "final_position": (x, y, z)}
        """
        if speed <= 0:
            raise ValueError("子弹初速度必须为正")

        dlen = math.sqrt(sum(c ** 2 for c in direction))
        if dlen == 0:
            raise ValueError("射击方向不能为零向量")

        vx = direction[0] / dlen * speed
        vy = direction[1] / dlen * speed
        vz = direction[2] / dlen * speed

        segments: List[Dict] = []
        seg_start = tuple(origin)
        position = [float(origin[0]), float(origin[1]), float(origin[2])]
        t = 0.0
        # 已经穿透的障碍物（穿透后子弹离开该障碍之前不再重复判定）
        passed_obstacles = set()  # type: set[int]

        def segment_hit(prev: Sequence[float], curr: Sequence[float], obs: Dict):
            """线段与球形障碍求交，返回进入点；未命中返回 None。"""
            center = obs["position"]
            radius = obs["radius"]
            dx = curr[0] - prev[0]
            dy = curr[1] - prev[1]
            dz = curr[2] - prev[2]
            seg_len2 = dx * dx + dy * dy + dz * dz
            if seg_len2 == 0:
                return None
            # 球心在线段上的投影参数
            frac = (
                (center[0] - prev[0]) * dx
                + (center[1] - prev[1]) * dy
                + (center[2] - prev[2]) * dz
            ) / seg_len2
            frac = max(0.0, min(1.0, frac))
            closest = (
                prev[0] + dx * frac,
                prev[1] + dy * frac,
                prev[2] + dz * frac,
            )
            if _distance3d(closest, center) > radius:
                return None
            return closest

        hit_terminal = False
        while t < max_flight_time:
            t += step
            vy -= self.gravity * step
            prev = tuple(position)
            position[0] += vx * step
            position[1] += vy * step
            position[2] += vz * step

            for idx, obs in enumerate(obstacles or []):
                if idx in passed_obstacles:
                    continue
                entry = segment_hit(prev, position, obs)
                if entry is None:
                    continue
                current = entry
                if obs.get("penetrable", False):
                    # 穿透：结束当前段，子弹继续飞行，从障碍另一侧开启新段
                    segments.append(
                        {
                            "start": seg_start,
                            "end": current,
                            "penetrated": True,
                        }
                    )
                    seg_start = current
                    passed_obstacles.add(idx)
                else:
                    segments.append(
                        {
                            "start": seg_start,
                            "end": current,
                            "penetrated": False,
                        }
                    )
                    seg_start = current
                    hit_terminal = True
                break

            if hit_terminal:
                break

        flight_time = t
        # 未命中任何阻挡物：整段为一条弹道
        if not hit_terminal:
            segments.append(
                {"start": seg_start, "end": tuple(position), "penetrated": False}
            )

        display_time = max(self.min_tracer_display_time, flight_time)
        return {
            "segments": segments,
            "flight_time": flight_time,
            "display_time": display_time,
            "final_position": tuple(position),
        }
