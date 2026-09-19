# AGENTS.md - astrbot_plugin_file_listener

## 沟通

- 与用户沟通默认使用简体中文。
- 代码标识符、命令、库名和协议名保持英文。

## 项目结构

- `main.py`：AstrBot 插件入口、生命周期和文件事件入口，仅保留薄适配层。
- `core/models.py`：公开文件事件、options、binding/filter 数据契约。
- `core/listener.py`：FilterChain、callback registry 与 dispatcher。
- `core/adapters/`：Telegram / OneBot 平台边界。
- `core/direct_link.py`：内置 DirectLink binding 与模板渲染。
- `core/dedupe.py`：生命周期内短时事件去重。
- `core/config.py`：插件配置到只读 ListenerOptions 的转换。
- `tests/`：仅测试公共行为和关键集成边界，避免实现细节测试。

## 开发规则

- 优先复用 AstrBot 原生 API，不引入不必要依赖。
- AstrBot 能力必须优先从 `astrbot.api` 及其子模块导入；只有确认 `astrbot.api` 未提供所需能力时，才允许使用 `astrbot.core` 等内部模块，并在对应代码附近说明为什么无法使用公开 API。
- 新增或修改 AstrBot import 时，必须先核对公开 API 是否已经提供等价接口，禁止仅因内部路径更直接而依赖内部实现。
- 日志统一使用 `astrbot.api.logger`，禁止直接使用 Python `logging`。
- 运行态数据必须写入 AstrBot 分配的数据目录，不写入插件源码目录。
- 保持 KISS/YAGNI；不增加 callback retry/timeout、后台 worker、持久化 registry、全局 service registry 等未确认能力。
- 新增注释使用中文，复杂函数使用 Google 风格 docstring。

## 测试

- 代码改动走 Red -> Green -> Refactor。
- 最小回归：`PYTHONPATH=.. /srv/AstrBot/.venv/bin/python -m pytest tests -q`。
- 插件入口或生命周期变更后，使用普通 AstrBot plugin loader 路径验证真实导入、初始化和卸载。
- `astrbot_plugin_dna` 的专用 lifecycle checker 会额外要求 LLM tools / Web API，不适合作为本插件的通过条件；禁止为了迎合它伪造无关接口。
- 完成后运行 `ruff check .` 和 `python3 -m compileall .`。

## 审查

- 优先检查正确性、回归、安全、生命周期资源泄漏和缺失验证。
- 发现问题时给出具体文件/行号；没有发现问题时明确说明验证范围。
