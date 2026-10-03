"""
命中判定与受击盒系统 - 核心模块（修复版）

修复内容：
- 受击盒区分头/胸/腿部位，并随姿态（站/蹲/跳）变化
- 伤害区域有优先级（头>胸>腿）和伤害倍率（爆头2倍）
- 延迟补偿回滚到客户端开枪时刻，有上限并做合理性校验
- 命中反馈包含部位、伤害数值、声音，并区分致命命中
- 弹道考虑飞行时间与重力下坠，显示时间足够，支持穿透多段
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
import time

Vector3 = Tuple[float, float, float]

# 伤害区域倍率与优先级（数值越大优先级越高）
ZONE_MULTIPLIERS: Dict[str, float] = {"head": 2.0, "chest": 1.0, "legs": 0.75}
ZONE_PRIORITY: Dict[str, int] = {"head": 3, "chest": 2, "legs": 1}

# 各姿态下的受击盒：(部位, 相对脚底高度, 半径)
STANCE_HITBOXES: Dict[str, List[Tuple[str, float, float]]] = {
    "stand": [("head", 1.70, 0.18), ("chest", 1.15, 0.35), ("legs", 0.45, 0.40)],
    "crouch": [("head", 1.05, 0.18), ("chest", 0.70, 0.32), ("legs", 0.25, 0.30)],
    "jump": [("head", 1.70, 0.18), ("chest", 1.15, 0.35), ("legs", 0.45, 0.40)],
}

# 延迟补偿
MAX_LAG_COMPENSATION_MS = 250.0   # 补偿上限
MAX_REASONABLE_PING_MS = 1000.0   # 超过视为伪造延迟
POSITION_HISTORY_SECONDS = 1.0    # 位置历史保留时长

# 弹道
GRAVITY = 9.8
MIN_TRACER_DISPLAY_TIME = 1.0     # 弹道最短显示时间（秒）
PENETRATION_SPEED_RETENTION = 0.7  # 每穿透一堵墙保留的速度比例


@dataclass
class Config:
    """配置项"""
    pass


def _sub(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vector3, s: float) -> Vector3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vector3, b: Vector3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a: Vector3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vector3) -> Vector3:
    n = _length(a)
    if n == 0:
        raise ValueError("zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


class HitRegistration:
    """命中判定与受击盒系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history: Dict[str, List[Tuple[float, Vector3]]] = {}

    def update(self, dt: float):
        """每帧更新"""
        pass

    def reset(self):
        """重置状态"""
        self._state = {}
        self._history = {}

    # ------------------------------------------------------------------
    # 受击盒：区分部位、随姿态变化
    # ------------------------------------------------------------------
    def get_hitboxes(self, position: Vector3, stance: str = "stand") -> List[Dict]:
        """根据脚底位置与姿态生成各部位受击盒（球形）。"""
        if stance not in STANCE_HITBOXES:
            raise ValueError(f"unknown stance: {stance}")
        boxes = []
        for zone, height, radius in STANCE_HITBOXES[stance]:
            center = (position[0], position[1] + height, position[2])
            boxes.append({"zone": zone, "center": center, "radius": radius})
        return boxes

    def _ray_sphere_t(self, origin: Vector3, direction: Vector3,
                      center: Vector3, radius: float) -> Optional[float]:
        """射线与球求交，返回最近的正t；未命中返回None。"""
        oc = _sub(origin, center)
        b = _dot(oc, direction)
        c = _dot(oc, oc) - radius * radius
        discriminant = b * b - c
        if discriminant < 0:
            return None
        sq = math.sqrt(discriminant)
        t = -b - sq
        if t < 0:
            t = -b + sq
        if t < 0:
            return None
        return t

    def check_hitbox(self, ray_origin: Vector3, ray_direction: Vector3,
                     target_position: Vector3, stance: str = "stand") -> Optional[str]:
        """检测射线命中目标的哪个部位；未命中返回None。

        返回最近命中点的部位名（"head"/"chest"/"legs"）。
        """
        direction = _normalize(ray_direction)
        best_t = None
        best_zone = None
        for box in self.get_hitboxes(target_position, stance):
            t = self._ray_sphere_t(ray_origin, direction, box["center"], box["radius"])
            if t is not None and (best_t is None or t < best_t):
                best_t = t
                best_zone = box["zone"]
        return best_zone

    # ------------------------------------------------------------------
    # 伤害区域：优先级 + 倍率
    # ------------------------------------------------------------------
    def hit_zone_damage(self, base_damage: float, zones_hit: List[str]) -> Dict:
        """根据子弹穿过的部位列表计算伤害。

        同时穿过多个部位时按优先级（头>胸>腿）取最高者，
        伤害 = 基础伤害 × 部位倍率（爆头2倍）。
        """
        valid = [z for z in zones_hit if z in ZONE_PRIORITY]
        if not valid:
            return {"zone": None, "damage": 0.0, "multiplier": 0.0}
        zone = max(valid, key=lambda z: ZONE_PRIORITY[z])
        multiplier = ZONE_MULTIPLIERS[zone]
        return {
            "zone": zone,
            "damage": base_damage * multiplier,
            "multiplier": multiplier,
        }

    # ------------------------------------------------------------------
    # 延迟补偿：回滚到客户端时刻，有上限，校验合理性
    # ------------------------------------------------------------------
    def record_position(self, player_id: str, position: Vector3,
                        timestamp: Optional[float] = None):
        """记录玩家位置历史，供延迟补偿回滚使用。"""
        ts = time.time() if timestamp is None else timestamp
        history = self._history.setdefault(player_id, [])
        history.append((ts, position))
        history.sort(key=lambda item: item[0])
        cutoff = ts - max(POSITION_HISTORY_SECONDS, MAX_LAG_COMPENSATION_MS / 1000.0 * 2)
        self._history[player_id] = [item for item in history if item[0] >= cutoff]

    def _position_at(self, player_id: str, timestamp: float) -> Optional[Vector3]:
        """按时间戳线性插值历史位置。"""
        history = self._history.get(player_id)
        if not history:
            return None
        if timestamp <= history[0][0]:
            return history[0][1]
        if timestamp >= history[-1][0]:
            return history[-1][1]
        for (t0, p0), (t1, p1) in zip(history, history[1:]):
            if t0 <= timestamp <= t1:
                if t1 == t0:
                    return p1
                ratio = (timestamp - t0) / (t1 - t0)
                return (
                    p0[0] + (p1[0] - p0[0]) * ratio,
                    p0[1] + (p1[1] - p0[1]) * ratio,
                    p0[2] + (p1[2] - p0[2]) * ratio,
                )
        return history[-1][1]

    def lag_compensation(self, player_id: str, client_timestamp: float,
                         reported_ping_ms: float,
                         server_time: Optional[float] = None) -> Dict:
        """延迟补偿：回滚到客户端开枪时刻的位置。

        - 使用客户端开枪时刻（client_timestamp）对应的历史位置，而非服务器当前位置
        - 回滚量上限 MAX_LAG_COMPENSATION_MS
        - 合理性校验：负延迟、超过 MAX_REASONABLE_PING_MS 的延迟、
          以及“未来时间戳”都视为伪造，拒绝补偿
        """
        now = time.time() if server_time is None else server_time

        if reported_ping_ms < 0 or reported_ping_ms > MAX_REASONABLE_PING_MS:
            return {"valid": False, "position": None, "rewind_ms": 0.0,
                    "reason": "unreasonable ping"}

        if client_timestamp > now:
            return {"valid": False, "position": None, "rewind_ms": 0.0,
                    "reason": "client timestamp in the future"}

        rewind_ms = (now - client_timestamp) * 1000.0
        # 回滚量不得超过上报延迟，也不得超过补偿上限
        rewind_ms = min(rewind_ms, reported_ping_ms, MAX_LAG_COMPENSATION_MS)
        target_time = now - rewind_ms / 1000.0

        position = self._position_at(player_id, target_time)
        if position is None:
            return {"valid": False, "position": None, "rewind_ms": 0.0,
                    "reason": "no position history"}

        return {"valid": True, "position": position, "rewind_ms": rewind_ms,
                "reason": "ok"}

    # ------------------------------------------------------------------
    # 命中反馈：部位 + 伤害数值 + 声音 + 致命区分
    # ------------------------------------------------------------------
    def hit_feedback(self, zone: str, damage: float,
                     is_lethal: bool = False) -> Dict:
        """生成命中反馈：准星颜色、命中部位、伤害数值、音效。"""
        if is_lethal:
            sound = "kill_confirm"
            crosshair_color = "red"
        elif zone == "head":
            sound = "headshot"
            crosshair_color = "orange"
        else:
            sound = "hit"
            crosshair_color = "white"
        return {
            "crosshair_color": crosshair_color,
            "hit_zone": zone,
            "damage_number": damage,
            "show_damage_number": True,
            "sound": sound,
            "is_lethal": is_lethal,
        }

    # ------------------------------------------------------------------
    # 弹道：飞行时间 + 重力下坠 + 足够显示时间 + 穿透
    # ------------------------------------------------------------------
    def projectile_tracers(self, origin: Vector3, direction: Vector3,
                           speed: float, max_range: float = 1000.0,
                           walls: Optional[List[Dict]] = None,
                           gravity: float = GRAVITY,
                           display_time: float = MIN_TRACER_DISPLAY_TIME,
                           max_penetrations: int = 2,
                           dt: float = 0.001) -> Dict:
        """模拟弹道并生成曳光显示数据。

        - 考虑子弹飞行时间与重力下坠
        - 显示时间不低于 MIN_TRACER_DISPLAY_TIME
        - 支持穿透：每堵墙消耗一次穿透次数并衰减速度，
          每段弹道都记录，穿透多堵墙时全部显示

        walls: [{"x": 墙所在x坐标, "thickness": 厚度}]，墙面垂直于x轴。
        返回 {"points", "segments", "flight_time", "display_time", "penetrations"}
        """
        walls = sorted(walls or [], key=lambda w: w["x"])
        velocity = _scale(_normalize(direction), speed)
        position = origin
        elapsed = 0.0
        penetrations = 0
        points: List[Vector3] = [position]
        segments: List[List[Vector3]] = [[position]]
        traveled = 0.0

        while traveled < max_range:
            prev = position
            velocity = (velocity[0], velocity[1] - gravity * dt, velocity[2])
            position = _add(position, _scale(velocity, dt))
            elapsed += dt
            traveled += _length(_sub(position, prev))

            hit_wall = None
            for wall in walls:
                half = wall["thickness"] / 2.0
                x0, x1 = wall["x"] - half, wall["x"] + half
                lo, hi = (prev[0], position[0]) if prev[0] <= position[0] else (position[0], prev[0])
                if lo <= x1 and hi >= x0 and not (prev[0] >= x0 and prev[0] <= x1):
                    hit_wall = wall
                    break

            if hit_wall is not None:
                if penetrations >= max_penetrations:
                    points.append(position)
                    segments[-1].append(position)
                    break
                penetrations += 1
                points.append(position)
                segments[-1].append(position)
                segments.append([position])
                velocity = _scale(velocity, PENETRATION_SPEED_RETENTION)
                # 跳过墙厚度，避免重复触发同一堵墙
                skip = hit_wall["thickness"] + 0.01
                step = _normalize(velocity)
                position = _add(position, _scale(step, skip))
                traveled += skip
                points.append(position)
                segments[-1].append(position)
                continue

            points.append(position)
            segments[-1].append(position)

        return {
            "points": points,
            "segments": segments,
            "flight_time": elapsed,
            "display_time": max(display_time, MIN_TRACER_DISPLAY_TIME),
            "penetrations": penetrations,
        }
