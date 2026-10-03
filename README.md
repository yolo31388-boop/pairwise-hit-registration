# 命中判定与受击盒系统

## 项目描述

命中判定与受击盒系统的bug修复项目。核心模块 `ballistics/hit_registration.py` 存在多个已知bug，需要修复后使测试全部通过。

## 已知问题

- 受击盒只有球形不区分部位不考虑姿态
- 伤害区域完全没做无优先级无倍率
- 延迟补偿用服务器当前位置无上限
- 命中反馈只有准星变色无声音无部位
- 弹道是直线不考虑飞行时间重力穿透

## 项目结构

```
├── ballistics/hit_registration.py       # 核心模块（带bug）
├── tests/test_hit_registration.py    # 验收测试（红态）
├── pyproject.toml          # 项目配置
└── README.md               # 说明文档
```

## 运行测试

```bash
python -m pytest tests/test_hit_registration.py -q
```

修复目标：所有 15 个测试全部通过。

## 技术要求

- 纯Python标准库，不引入第三方依赖
- 不修改测试文件
- 保持现有API接口不变
- 修复所有已知bug
