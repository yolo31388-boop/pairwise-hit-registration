"""
命中判定与受击盒系统 - 验收测试

运行方式：python -m pytest tests/test_hit_registration.py -q
共 15 个测试用例：
- 1-3:  受击盒分部位 + 姿态
- 4-6:  伤害区域优先级与倍率
- 7-9:  延迟补偿（客户端位置 / 上限 / 合理性校验）
- 10-12: 命中反馈（部位伤害显示 / 声音 / 致命区分）
- 13-15: 弹道（重力 / 显示时间 / 穿透）
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ballistics.hit_registration import HitRegistration


class TestHitRegistration:
    """命中判定与受击盒系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = HitRegistration()

    # ---------- 受击盒：分部位 + 姿态 ----------

    def test_case_01(self):
        """头部位置的子弹判定为头部命中，而非统一球形盒"""
        target = (0.0, 0.0, 0.0)
        head_shot = (0.0, 1.7, 0.0)
        result = self.system.check_hitbox(head_shot, target, "standing")
        assert result["hit"] is True
        assert result["zone"] == "head"

    def test_case_02(self):
        """蹲下姿态受击盒高度变小：站姿头部高度打空，真正蹲姿头部才命中"""
        target = (0.0, 0.0, 0.0)
        at_standing_head = (0.0, 1.7, 0.0)
        # 蹲着时该高度（1.7 > 蹲姿盒高 1.1）必须打空
        crouch_result = self.system.check_hitbox(at_standing_head, target, "crouching")
        assert crouch_result["hit"] is False
        # 蹲姿头部（贴近 1.1 盒顶）应判定为头部
        crouch_head = self.system.check_hitbox((0.0, 1.0, 0.0), target, "crouching")
        assert crouch_head["hit"] is True
        assert crouch_head["zone"] == "head"

    def test_case_03(self):
        """目标旁边的空气（水平方向超出受击盒半径）不能命中；腿部点判定为腿部"""
        target = (0.0, 0.0, 0.0)
        in_air = (5.0, 1.0, 0.0)
        air_result = self.system.check_hitbox(in_air, target, "standing")
        assert air_result["hit"] is False
        assert air_result["zone"] is None

        legs = self.system.check_hitbox((0.0, 0.3, 0.0), target, "standing")
        assert legs["hit"] is True
        assert legs["zone"] == "legs"

    # ---------- 伤害区域：优先级 + 倍率 ----------

    def test_case_04(self):
        """爆头伤害为基础伤害的 2 倍"""
        result = self.system.hit_zone_damage(100.0, ["head"])
        assert result["zone"] == "head"
        assert math.isclose(result["damage"], 200.0)
        assert math.isclose(result["multiplier"], 2.0)

    def test_case_05(self):
        """子弹同时穿过头部和胸部时，按优先级选取头部而非随机"""
        result = self.system.hit_zone_damage(100.0, ["chest", "head"])
        assert result["zone"] == "head"
        assert math.isclose(result["damage"], 200.0)
        # 反向顺序结果一致，保证不是随机/首匹配
        reversed_result = self.system.hit_zone_damage(100.0, ["head", "chest"])
        assert reversed_result["zone"] == "head"

    def test_case_06(self):
        """胸部为 1 倍、腿部低于 1 倍，未命中任何部位伤害为 0"""
        chest = self.system.hit_zone_damage(80.0, ["chest"])
        assert math.isclose(chest["damage"], 80.0)
        assert math.isclose(chest["multiplier"], 1.0)

        legs = self.system.hit_zone_damage(80.0, ["legs"])
        assert legs["multiplier"] < 1.0
        assert math.isclose(legs["damage"], 60.0)

        miss = self.system.hit_zone_damage(80.0, [])
        assert miss["damage"] == 0.0
        assert miss["zone"] is None

    # ---------- 延迟补偿：客户端位置 / 上限 / 合理性校验 ----------

    def test_case_07(self):
        """命中判定使用客户端开枪时的位置，而非服务器当前位置"""
        client_pos = (10.0, 0.0, 0.0)
        server_pos = (12.0, 0.0, 0.0)
        result = self.system.lag_compensation(client_pos, server_pos, 50.0)
        assert result["valid"] is True
        assert result["position"] == client_pos
        assert result["position"] != server_pos

    def test_case_08(self):
        """延迟补偿有上限：500ms 延迟也只补偿到上限（默认 200ms）"""
        client_pos = (1.0, 0.0, 0.0)
        server_pos = (0.0, 0.0, 0.0)
        result = self.system.lag_compensation(client_pos, server_pos, 500.0)
        assert result["valid"] is True
        assert result["applied_latency_ms"] <= self.system.max_latency_ms
        assert math.isclose(result["applied_latency_ms"], 200.0)
        # 上限以内的延迟按实际值补偿
        normal = self.system.lag_compensation(client_pos, server_pos, 80.0)
        assert math.isclose(normal["applied_latency_ms"], 80.0)

    def test_case_09(self):
        """合理性校验：负延迟与位置偏差过大（伪造延迟）拒绝补偿"""
        server_pos = (0.0, 0.0, 0.0)
        bad_latency = self.system.lag_compensation((1.0, 0.0, 0.0), server_pos, -50.0)
        assert bad_latency["valid"] is False
        assert bad_latency["position"] == tuple(server_pos)

        faked = self.system.lag_compensation((100.0, 0.0, 0.0), server_pos, 150.0)
        assert faked["valid"] is False
        assert faked["position"] == tuple(server_pos)

    # ---------- 命中反馈：部位 + 伤害数值 + 声音 + 致命区分 ----------

    def test_case_10(self):
        """命中反馈包含命中部位与伤害数值，而不仅是准星变色"""
        feedback = self.system.hit_feedback("head", 200.0, 100.0)
        assert feedback["hit"] is True
        assert feedback["zone"] == "head"
        assert feedback["damage"] == 200.0
        assert feedback["zone_label"] == "头部"

    def test_case_11(self):
        """命中反馈带有声音事件"""
        normal = self.system.hit_feedback("chest", 30.0, 100.0)
        assert normal["sound"] == "hit"
        headshot = self.system.hit_feedback("head", 50.0, 100.0)
        assert headshot["sound"] == "headshot"

    def test_case_12(self):
        """致命命中与普通命中反馈不同（致命标记 + 专属声音/颜色）"""
        lethal = self.system.hit_feedback("chest", 100.0, 100.0)
        assert lethal["lethal"] is True
        assert lethal["sound"] == "kill"
        assert lethal["crosshair_color"] == "red"

        normal = self.system.hit_feedback("chest", 30.0, 100.0)
        assert normal["lethal"] is False
        assert normal["sound"] == "hit"
        assert normal["crosshair_color"] != "red"

    # ---------- 弹道：飞行时间 + 重力 + 穿透 ----------

    def test_case_13(self):
        """弹道考虑飞行时间与重力：水平射击的子弹末端低于枪口"""
        origin = (0.0, 10.0, 0.0)
        result = self.system.projectile_tracers(
            origin, (1.0, 0.0, 0.0), speed=100.0, max_flight_time=1.0
        )
        assert result["flight_time"] >= 1.0
        # 重力下垂直落差应约为 0.5*g*t^2，留数值积分误差余量
        drop = origin[1] - result["final_position"][1]
        assert drop > 4.0
        # 水平方向按速度前进
        assert result["final_position"][0] > 90.0

    def test_case_14(self):
        """弹道显示时间足够长，不能写死 0.1 秒"""
        result = self.system.projectile_tracers(
            (0.0, 1.7, 0.0), (1.0, 0.0, 0.0), speed=200.0, max_flight_time=0.1
        )
        assert result["display_time"] >= self.system.min_tracer_display_time
        assert result["display_time"] >= 0.5
        assert result["display_time"] > 0.1

    def test_case_15(self):
        """弹道支持穿透：穿过两堵可穿透墙后生成多段弹道，且全程考虑重力"""
        obstacles = [
            {"position": (20.0, 1.7, 0.0), "radius": 0.5, "penetrable": True},
            {"position": (50.0, 1.7, 0.0), "radius": 0.5, "penetrable": True},
        ]
        result = self.system.projectile_tracers(
            (0.0, 1.7, 0.0),
            (1.0, 0.0, 0.0),
            speed=300.0,
            obstacles=obstacles,
            max_flight_time=1.0,
        )
        # 墙1 -> 墙2 -> 终点，共 3 段
        assert len(result["segments"]) == 3
        assert result["segments"][0]["penetrated"] is True
        assert result["segments"][1]["penetrated"] is True
        # 各段首尾相连
        first = result["segments"][0]
        second = result["segments"][1]
        assert first["end"] == second["start"]
        # 不可穿透障碍应在首段即终止
        blocked = self.system.projectile_tracers(
            (0.0, 1.7, 0.0),
            (1.0, 0.0, 0.0),
            speed=300.0,
            obstacles=[
                {"position": (20.0, 1.7, 0.0), "radius": 0.5, "penetrable": False}
            ],
            max_flight_time=1.0,
        )
        assert len(blocked["segments"]) == 1
        assert blocked["segments"][0]["penetrated"] is False
        assert blocked["segments"][0]["end"][0] < 25.0


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
