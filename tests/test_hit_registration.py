"""
命中判定与受击盒系统 - 验收测试

运行方式：python -m pytest tests/test_hit_registration.py -q
共 15 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ballistics.hit_registration import (
    HitRegistration,
    MAX_LAG_COMPENSATION_MS,
    MIN_TRACER_DISPLAY_TIME,
    ZONE_MULTIPLIERS,
)


class TestHitRegistration:
    """命中判定与受击盒系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = HitRegistration()

    # ---------------- 受击盒：部位区分 + 姿态 ----------------

    def test_case_01(self):
        """受击盒区分部位：命中头部判定为 head"""
        zone = self.system.check_hitbox(
            ray_origin=(0, 1.7, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="stand")
        assert zone == "head"

    def test_case_02(self):
        """受击盒区分部位：命中胸部和腿部分别判定为 chest/legs"""
        chest = self.system.check_hitbox(
            ray_origin=(0, 1.15, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="stand")
        legs = self.system.check_hitbox(
            ray_origin=(0, 0.45, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="stand")
        assert chest == "chest"
        assert legs == "legs"

    def test_case_03(self):
        """受击盒考虑姿态：蹲下后原站立头部高度打不中"""
        standing = self.system.check_hitbox(
            ray_origin=(0, 1.7, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="stand")
        crouching = self.system.check_hitbox(
            ray_origin=(0, 1.7, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="crouch")
        assert standing == "head"
        assert crouching is None

    def test_case_04(self):
        """受击盒大小精确：打在目标旁边的空气中不判定命中"""
        zone = self.system.check_hitbox(
            ray_origin=(1.5, 1.15, -5), ray_direction=(0, 0, 1),
            target_position=(0, 0, 0), stance="stand")
        assert zone is None

    # ---------------- 伤害区域：优先级 + 倍率 ----------------

    def test_case_05(self):
        """伤害倍率：爆头伤害为基础伤害的2倍"""
        result = self.system.hit_zone_damage(base_damage=50, zones_hit=["head"])
        assert result["zone"] == "head"
        assert result["multiplier"] == 2.0
        assert result["damage"] == pytest.approx(100.0)

    def test_case_06(self):
        """伤害区域优先级：同时穿过头部和胸部时按头部结算"""
        result = self.system.hit_zone_damage(base_damage=50,
                                             zones_hit=["chest", "head"])
        assert result["zone"] == "head"
        assert result["damage"] == pytest.approx(50 * ZONE_MULTIPLIERS["head"])

    def test_case_07(self):
        """伤害倍率：腿部伤害低于身体，且不同部位伤害不同"""
        legs = self.system.hit_zone_damage(base_damage=50, zones_hit=["legs"])
        chest = self.system.hit_zone_damage(base_damage=50, zones_hit=["chest"])
        assert legs["damage"] < chest["damage"]
        assert legs["damage"] == pytest.approx(50 * ZONE_MULTIPLIERS["legs"])

    # ---------------- 延迟补偿：客户端位置 + 上限 + 校验 ----------------

    def _record_moving_player(self, player_id="p1"):
        """记录一个匀速移动的玩家：t=0在x=0，t=1在x=4"""
        self.system.record_position(player_id, (0, 0, 0), timestamp=0.0)
        self.system.record_position(player_id, (1, 0, 0), timestamp=0.25)
        self.system.record_position(player_id, (2, 0, 0), timestamp=0.5)
        self.system.record_position(player_id, (4, 0, 0), timestamp=1.0)

    def test_case_08(self):
        """延迟补偿使用客户端开枪时刻的位置，而非服务器当前位置"""
        self._record_moving_player()
        result = self.system.lag_compensation(
            "p1", client_timestamp=0.0, reported_ping_ms=100, server_time=1.0)
        assert result["valid"] is True
        # 回滚100ms：位置应在t=0.9时刻(x=3.6)，而不是当前位置x=4
        assert result["position"][0] == pytest.approx(3.6)
        assert result["position"][0] != pytest.approx(4.0)

    def test_case_09(self):
        """延迟补偿有上限：500ms延迟最多只回滚 MAX_LAG_COMPENSATION_MS"""
        self._record_moving_player()
        result = self.system.lag_compensation(
            "p1", client_timestamp=0.5, reported_ping_ms=500, server_time=1.0)
        assert result["valid"] is True
        assert result["rewind_ms"] == pytest.approx(MAX_LAG_COMPENSATION_MS)
        # 只回滚250ms到t=0.75(x=3)，而不是完整500ms到t=0.5(x=2)
        assert result["position"][0] == pytest.approx(3.0)

    def test_case_10(self):
        """延迟补偿合理性校验：伪造延迟（负值/超大值/未来时间戳）被拒绝"""
        self._record_moving_player()
        negative = self.system.lag_compensation(
            "p1", client_timestamp=0.5, reported_ping_ms=-50, server_time=1.0)
        absurd = self.system.lag_compensation(
            "p1", client_timestamp=0.5, reported_ping_ms=5000, server_time=1.0)
        future = self.system.lag_compensation(
            "p1", client_timestamp=2.0, reported_ping_ms=100, server_time=1.0)
        assert negative["valid"] is False
        assert absurd["valid"] is False
        assert future["valid"] is False

    # ---------------- 命中反馈：部位 + 伤害 + 声音 + 致命区分 ----------------

    def test_case_11(self):
        """命中反馈显示命中部位和伤害数值"""
        feedback = self.system.hit_feedback(zone="head", damage=100)
        assert feedback["hit_zone"] == "head"
        assert feedback["damage_number"] == 100
        assert feedback["show_damage_number"] is True

    def test_case_12(self):
        """命中反馈有声音，且致命命中与普通命中反馈不同"""
        normal = self.system.hit_feedback(zone="chest", damage=30, is_lethal=False)
        lethal = self.system.hit_feedback(zone="chest", damage=30, is_lethal=True)
        assert normal["sound"]
        assert lethal["sound"]
        assert normal["sound"] != lethal["sound"]
        assert normal["crosshair_color"] != lethal["crosshair_color"]
        assert lethal["is_lethal"] is True
        assert normal["is_lethal"] is False

    # ---------------- 弹道：飞行时间 + 重力 + 显示时间 + 穿透 ----------------

    def test_case_13(self):
        """弹道考虑飞行时间和重力：水平射击的子弹会下坠"""
        tracer = self.system.projectile_tracers(
            origin=(0, 1.7, 0), direction=(1, 0, 0), speed=100, dt=0.01)
        end = tracer["points"][-1]
        assert tracer["flight_time"] > 0
        # 水平射出的子弹受重力影响，终点高度明显低于枪口
        assert end[1] < 1.7 - 1.0

    def test_case_14(self):
        """弹道显示时间足够长，不是写死的0.1秒"""
        tracer = self.system.projectile_tracers(
            origin=(0, 1.7, 0), direction=(1, 0, 0), speed=100,
            display_time=0.1, dt=0.01)
        assert tracer["display_time"] >= MIN_TRACER_DISPLAY_TIME
        assert tracer["display_time"] > 0.1

    def test_case_15(self):
        """弹道支持穿透：穿过两堵墙的子弹显示全部三段弹道"""
        walls = [{"x": 10, "thickness": 0.2}, {"x": 30, "thickness": 0.2}]
        tracer = self.system.projectile_tracers(
            origin=(0, 1.7, 0), direction=(1, 0, 0), speed=100,
            walls=walls, max_penetrations=2, dt=0.01)
        assert tracer["penetrations"] == 2
        assert len(tracer["segments"]) == 3
        # 穿透后子弹继续飞行，终点超过第二堵墙
        assert tracer["points"][-1][0] > 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
