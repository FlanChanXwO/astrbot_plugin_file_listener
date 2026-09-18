# AGENTS.md - astrbot_plugin_file_listener

## 沟通

- 与用户沟通默认使用简体中文。
- 代码标识符、命令、库名和协议名保持英文。

## 项目结构

- `main.py`：AstrBot 插件入口、生命周期和命令注册，仅保留薄适配层。
- `core/`：通用日志、路径/基础工具以及后续可复用能力。
- `tests/`：仅测试公共行为和关键集成边界，避免实现细节测试。

## 开发规则

- 优先复用 AstrBot 原生 API，不引入不必要依赖。
- 日志统一使用 `astrbot.api.logger`，禁止直接使用 Python `logging`。
- 运行态数据必须写入 AstrBot 分配的数据目录，不写入插件源码目录。
- 保持 KISS/YAGNI；当前仅实现已确认的 `echo` 测试命令。
- 新增注释使用中文，复杂函数使用 Google 风格 docstring。

## 测试

- 代码改动走 Red -> Green -> Refactor。
- 最小回归：`python3 -m pytest tests -q`。
- 插件入口或生命周期变更后，使用 `astrbot_plugin_dna` 的生命周期检查器验证真实 AstrBot 加载/卸载。
- 完成后运行 `ruff check .` 和 `python3 -m compileall .`。

## 审查

- 优先检查正确性、回归、安全、生命周期资源泄漏和缺失验证。
- 发现问题时给出具体文件/行号；没有发现问题时明确说明验证范围。
