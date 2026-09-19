# astrbot_plugin_file_listener 实施计划

日期：2026-09-19

依据：docs/superpowers/specs/2026-09-19-file-listener-design.md

## 0. 实施约束

本计划只实现已批准的首期范围：Telegram + OneBot 文件监听、FileEventBatch、每 callback 独立 FilterChain、短时去重、DirectLink、配置与生命周期。

- 使用独立 feature worktree 实施，不直接在 main 上开发。
- 严格 Red → Green → Refactor。
- main.py 保持薄，只负责 AstrBot 生命周期、配置、事件入口和公开 accessor。
- AstrBot 能力优先从 astrbot.api 导入；平台专用能力只允许出现在 adapter 边界。
- 不增加第三方运行时依赖。
- 不增加 callback timeout、retry、后台 worker、持久化 registry、跨重载通知总线。
- 测试公共契约和关键平台边界，不为简单 dataclass/getter/helper 建碎片化测试。

建议分支：feat/file-listener-core

建议 worktree：.worktrees/file-listener-core

---

## Task 1：核心事件模型与公共类型

### 文件

新增：

~~~text
core/models.py
tests/test_listener.py
~~~

修改：

~~~text
core/__init__.py
~~~

### Red

tests/test_listener.py 先验证：

1. FileEventBatch.files 为不可变 tuple。
2. FilterContext.current_files 是 binding 私有工作集。
3. FilterContext.original_batch 保留源 batch。
4. ListenerOptions.parallel 默认语义为 True。
5. CallbackBinding 只绑定一个 callback 和最多一条 FilterChain。

### Green

core/models.py 实现：

~~~text
FileEvent
FileEventBatch
FilterContext
ListenerOptions
FilterSpec
CallbackBinding
~~~

要求：

- FileEvent、FileEventBatch、ListenerOptions 优先 frozen dataclass。
- FilterContext 可变，但 original_batch 不提供替换语义。
- source_type 保持字符串，不做跨平台死枚举。
- callback/filter 类型只使用标准库 typing/collections.abc。

### 验证

~~~bash
python3 -m pytest tests/test_listener.py -q
ruff check core/models.py tests/test_listener.py
~~~

完成条件：后续 listener/filter/adapter 都只围绕这些公共类型工作。

---

## Task 2：FilterChain、注册表与 Dispatcher

### 文件

新增：

~~~text
core/listener.py
~~~

修改：

~~~text
tests/test_listener.py
core/__init__.py
~~~

### Red

使用手工构造的 FileEventBatch 覆盖：

1. batch 内多个文件时，一个 callback 只调用一次。
2. 多个 CallbackBinding 都收到同一源事件。
3. 每个 binding 拥有独立 FilterContext。
4. filter 按 priority 从小到大执行。
5. 同 priority 保持注册顺序。
6. filter 必须返回传入的同一个 FilterContext 实例。
7. continue_chain=False 只停止当前 chain。
8. current_files 为空时不执行当前 callback。
9. callback A 失败不阻止 B/C。
10. filter A 失败只终止 A 所属 binding。
11. parallel=True 并发执行完整 binding pipeline。
12. parallel=False 串行执行，但失败仍继续后续 binding。
13. 同一 callback 在 listener 生命周期内不能重复注册。
14. register(bindings=[...]) 任一项非法时整批不注册。
15. RegistrationHandle.unregister() 幂等。
16. listener close 后 registry 清空，旧 handle 失效。

并发测试只验证实际重叠，不依赖脆弱的固定毫秒顺序。

### Green

core/listener.py 实现：

~~~text
FilterChain
RegistrationHandle
FileListener
~~~

规则：

- register 在修改 registry 前完成整批校验。
- callback 唯一性只作用于当前 listener 生命周期。
- filter identity 使用 returned is context 校验。
- filter/callback 异常使用 AstrBot logger 记录并隔离。
- parallel=True 只在当前事件生命周期中并发等待，不创建 detached task。
- callback 收到由 current_files 派生的新 FileEventBatch。
- 不实现 retry/timeout。

### 验证

~~~bash
python3 -m pytest tests/test_listener.py -q
ruff check core/listener.py tests/test_listener.py
~~~

---

## Task 3：短时去重器

### 文件

新增：

~~~text
core/dedupe.py
tests/test_dedupe.py
~~~

修改 core/__init__.py。

### Red

覆盖：

1. 相同可信 event_id 在窗口内判重。
2. platform_instance + chat_id + file_id 是首选身份。
3. 无 file_id 时退化到 URL。
4. 再退化到 file_name + file_size。
5. 多文件 fingerprint 不受文件顺序影响。
6. OneBot message 与 group_upload_notice 可作为 mirror family 去重。
7. 普通上传与 merged_forward 不互相去重。
8. 只有弱身份时，不压制同 source 的连续事件。
9. 窗口过后同文件可再次触发。
10. enabled=False 完全 bypass。
11. 过期项通过正常处理路径惰性清理。

测试使用注入时钟或显式时间，不使用真实 sleep。

### Green

实现生命周期级内存 Deduplicator：

~~~text
event-id cache
fingerprint cache
timestamp lazy eviction
~~~

不落盘、不建后台清理协程。

本任务测试显式传入 window；不要在这里拍脑袋确定产品默认值。

---

## Task 4：Telegram / OneBot 普通文件适配

### 文件

新增：

~~~text
core/adapters/__init__.py
core/adapters/telegram.py
core/adapters/onebot.py
tests/test_adapters.py
~~~

### Red

Telegram：

1. 普通 document/File component → source_type=message。
2. forwarded document → source_type=forwarded。
3. 正确填充 name/url/size/sender/chat/platform instance。
4. 无 URL 时保留 FileEvent(file_url=None)。
5. 非文件事件返回 None。

OneBot：

1. 群聊 File component → source_type=message。
2. 私聊 File component → source_type=private_file。
3. 两种 source 分类互斥。
4. 同一源事件多个 File component 形成一个 batch。
5. 非文件事件返回 None。

### Green

每个平台 adapter 只承担：

~~~text
classify(event) -> source | None
extract(event, source, options) -> FileEventBatch | None
~~~

不强制抽象基类。

优先使用：

- event.get_messages()
- astrbot.api.message_components.File
- AstrBot 公开 sender/session/platform API

只有公开组件缺字段时才读取 event.message_obj.raw_message。

### AstrBot API 门禁

实现前再次核对 astrbot.api。

若确实必须 import astrbot.core：

1. 只允许发生在 core/adapters/。
2. 代码旁注明公开 API 缺失原因。
3. tests/test_architecture.py 改成明确 allowlist，而不是直接删除保护。

若无需内部 import，则保持当前架构测试不变。

---

## Task 5：OneBot group_upload 与 merged forward

### 文件

修改：

~~~text
core/adapters/onebot.py
tests/test_adapters.py
~~~

### Red

覆盖：

1. post_type=notice + notice_type=group_upload 形成 group_upload_notice batch。
2. file.id/name/size 正确进入 FileEvent。
3. notice 有 file_id 无 URL 时尝试 OneBot 文件 URL action。
4. URL action 失败仍保留 file_url=None。
5. 禁用 group_upload_notice 时不调用 URL action。
6. incoming Forward 仅在 merged_forward 开启时调用 get_forward_msg。
7. 一个 forward 内多个文件仍只形成一个 batch。
8. 嵌套 forward 遵守 forward_max_depth。
9. 深层节点失败不丢失已提取文件。
10. forward 文件缺 URL 时尽力补 URL，但不下载完整文件。

### Green

优先窄范围 duck typing：

~~~text
event.message_obj.raw_message
event.bot 或当前公开可用平台调用入口
~~~

优先避免 import 具体 AiocqhttpMessageEvent 内部类；确实不可避免时按 Task 4 allowlist 规则处理。

平台 action 异常只记录并降级，不让整个 AstrBot 事件链失败。

Source Gate 必须发生在 get_forward_msg 等昂贵调用之前。

---

## Task 6：DirectLink 内置 Binding

### 文件

新增：

~~~text
core/direct_link.py
tests/test_direct_link.py
~~~

修改 core/__init__.py。

### Red

覆盖：

1. 默认三行模板。
2. file_size=None 时删除包含 {file_size} 的整行。
3. 支持 {file_name}、{file_size}、{file_url}。
4. 未知占位符由配置校验拦截。
5. file_url=None 的文件跳过发送，但不影响第三方 callback。
6. smart：单文件单条，多文件聚合。
7. aggregate：整个 batch 一条。
8. separate：每个可发送文件一条。
9. 多文件聚合逐文件复用同一模板。
10. send_direct_link=False 时不发送。
11. SendDirectLinkFilter 返回同一个 FilterContext。
12. DirectLink terminal callback 不重复发送。
13. 系统 SendDirectLinkFilter 固定第 0 位，不参与普通 priority 排序。

### Green

core/direct_link.py 只负责：

~~~text
template validation
per-file render
reply-mode composition
SendDirectLinkFilter
DirectLink binding factory
~~~

不要引入模板引擎，只做受控占位符替换。

原始 URL 直接使用 adapter 的 file_url，不做代理或下载换 URL。

---

## Task 7：插件配置与 ListenerOptions

### 文件

新增：

~~~text
_conf_schema.json
core/config.py
tests/test_plugin_integration.py
~~~

修改 metadata.yaml。

### 配置结构

~~~text
runtime
monitor_sources
direct_link
dedupe
~~~

runtime：

~~~text
parallel=true
forward_max_depth=3
~~~

monitor_sources 默认全部开启：

~~~text
telegram: message, forwarded
onebot: message, private_file, group_upload_notice, merged_forward
~~~

direct_link：

~~~text
enabled=true
reply_mode=smart
template=已批准三行模板
~~~

template schema：

~~~text
type: string
editor_mode: true
editor_language: plaintext
~~~

dedupe：

~~~text
enabled=true
window_seconds=Task 9 实测后确定
~~~

### 配置解析

core/config.py 只做配置边界转换与校验：

- template 未知占位符：warning + 回退默认模板。
- 非法 reply_mode、depth 等局部值：warning + 字段默认值。
- 配置整体无法解析才让初始化失败。
- 不实现运行时 observer/hot reload。

### metadata

最终必须为：

~~~yaml
support_platforms:
  - telegram
  - aiocqhttp
astrbot_version: ">4.26.0"
~~~

其它平台不声明。

### 高价值配置测试

- 默认配置可构造 ListenerOptions。
- parallel 默认 True。
- monitor sources 默认全开。
- template 错误回退。
- metadata 平台和最低版本声明正确。

---

## Task 8：接入 main.py 生命周期与事件入口

### 文件

修改：

~~~text
main.py
core/__init__.py
tests/test_plugin_integration.py
~~~

删除：

~~~text
tests/test_echo.py
commands.json
~~~

如果 PathUtils 不再有真实用途，同时删除 core/utils.py 及对应 export。

### main.py 只负责

1. 接收 Context 和 AstrBotConfig。
2. 构造 ListenerOptions。
3. 初始化 Telegram/OneBot adapters。
4. 初始化 Deduplicator。
5. 初始化 FileListener。
6. 注册 DirectLink 内置 binding。
7. 暴露 get_file_listener()。
8. 使用 @filter.event_message_type(filter.EventMessageType.ALL) 接收 AstrBot 事件。
9. 执行 classify → source gate → extract → dedupe → dispatch。
10. terminate 时关闭 listener、清理 dedupe 与 adapter 状态。

### Red：完整 tracer bullet

~~~text
fake AstrMessageEvent
  → plugin event entry
  → adapter
  → FileEventBatch
  → dedupe
  → DirectLink + external callback
~~~

验证：

- 单文件 callback 一次。
- 多文件 batch callback 仍一次。
- dedupe 在所有 binding 前。
- DirectLink 与第三方 binding 独立。
- terminate 后 registry 清空。
- get_file_listener() 返回当前实例。

### 清理 echo

正式入口工作后删除：

- COMMAND_REGISTRY。
- echo handler。
- tests/test_echo.py。
- 只为旧 checker 服务的 commands.json。

不要为了旧 DNA 专用 checker 保留假命令或伪 API。

---

## Task 9：真实 OneBot/NapCat 时序验证并确定 dedupe 默认值

### 目标

用真实数据确定 dedupe.window_seconds。

### 采集

在当前 AstrBot + OneBot/NapCat 环境至少做多次测试群文件上传，记录：

~~~text
message file event 到达时间
group_upload_notice 到达时间
二者 delta
file_id/name/size/url 可用性
~~~

要求：

- 不永久记录无关聊天内容。
- 覆盖多次正常上传。
- 默认值取“实测正常最大间隔 + 小幅安全裕量”。
- 如果实际上只产生一个来源，记录事实并选择保守的小窗口。

### 回写

修改：

~~~text
_conf_schema.json
core/config.py（如有默认常量）
tests/test_dedupe.py
tests/test_plugin_integration.py
~~~

同一次上传必须只产生一次 DirectLink 和一次第三方 callback；窗口后再次上传同文件必须可再次触发。

---

## Task 10：真实 Telegram / OneBot E2E 与生命周期

### Telegram

验证：

1. 群聊普通文件。
2. 私聊普通文件。
3. forwarded document。
4. 文件名、大小（可得时）、URL 回复格式。
5. smart/aggregate/separate。
6. direct_link.enabled=false。

特别确认实际 file_url 是否为可访问的 Telegram 下载 URL；若标准 File component 只给本地路径，则只在 Telegram adapter 边界从 raw update 补齐，不把 token/url 逻辑扩散到核心层。

### OneBot

验证：

1. 群文件普通 message。
2. 私聊文件。
3. group_upload notice。
4. merged forward 多文件。
5. 嵌套 forward 最大深度。
6. URL action 失败仍把 file_url=None 交给 callback。
7. source 开关关闭后不发生对应平台 action。

### 生命周期

使用普通 AstrBot plugin loader harness：

~~~text
load
→ initialize
→ dispatch
→ register/unregister external callback
→ terminate
~~~

验证无 registry 泄漏、dedupe 泄漏、detached task；旧 RegistrationHandle 不作用于新实例。

DNA 专用 checker 如果因 LLM tools/Web API 要求报错，不添加伪接口绕过。

---

## Task 11：文档与脚手架清理

### 文件

修改：

~~~text
README.md
AGENTS.md
core/__init__.py
~~~

README 至少说明：

- Telegram / OneBot 支持范围。
- 默认 DirectLink。
- 配置概览。
- Context.get_registered_star(...) + get_file_listener() 获取 listener。
- CallbackBinding / FilterChain 最小示例。
- File Listener reload 后第三方需要重新注册。
- Telegram 原始 URL 可能包含 bot token 的风险。

AGENTS.md：

- 删除“当前仅实现 echo”的过期描述。
- 保留公共 API 优先、TDD、KISS/YAGNI。
- 生命周期验证改为普通 plugin loader harness，不再强制 DNA 专用 checker。

不生成 SUMMARY/报告类额外文档。

---

## Task 12：最终质量门禁

### 自动验证

~~~bash
python3 -m pytest tests -q
ruff format .
ruff check .
python3 -m compileall .
~~~

如果 ruff format 产生改动，再运行：

~~~bash
python3 -m pytest tests -q
ruff check .
~~~

### 架构审查

逐项确认：

- main.py 仍是薄入口。
- 平台差异没有泄漏到 listener/dispatcher。
- 没有全局 service registry。
- 没有 detached tasks。
- 一个 callback 最多一条 FilterChain。
- 一个源事件对同一 callback 最多一次。
- 每个 binding 独立 FilterContext。
- SendDirectLinkFilter 固定第一位。
- filter 不对原 event 留下不可逆修改。
- callback/filter 异常隔离。
- Source Gate 在昂贵请求之前。
- dedupe 在所有 binding 之前。
- 没有测试专用生产 API。
- 没有不必要的 adapter 基类、通用 event bus 或 task framework。

### Git

~~~bash
git status --short
git diff --check
~~~

不得提交调试日志、临时 fixture、抓包文件或运行态数据。

---

## 推荐提交切分

~~~text
feat: add file listener event model
feat: add callback binding dispatcher
feat: add file event deduplication
feat: add telegram and onebot adapters
feat: support onebot upload notices and forwards
feat: add direct link filter
feat: add file listener configuration
feat: wire file listener plugin lifecycle
test: verify platform file event integration
docs: document file listener public api
~~~

以“独立可通过测试的行为增量”为准，不为匹配列表强行拆提交。

---

## 最终验收清单

- [ ] Telegram 普通/转发文件 → FileEventBatch
- [ ] OneBot 群聊、私聊、group_upload、merged_forward → FileEventBatch
- [ ] 一个源事件对每个 callback 最多一次
- [ ] 多 callback 可同时注册
- [ ] 每 callback 最多一条独立 FilterChain
- [ ] FilterContext identity 在单 chain 内不变
- [ ] current_files 为空时不调用 callback
- [ ] callback/filter 异常隔离
- [ ] 默认完整 binding pipeline 并行
- [ ] DirectLink 默认启用且系统 filter 固定第一位
- [ ] smart/aggregate/separate 正常
- [ ] 缺 size 时删除模板整行
- [ ] 缺 URL 时不丢 FileEvent
- [ ] mirror 短时去重，forward 不误去重
- [ ] dedupe 默认窗口来自真实 OneBot/NapCat 时序
- [ ] source 关闭后不发生对应昂贵平台调用
- [ ] terminate 清理 registry/dedupe/handle
- [ ] metadata 仅声明 telegram、aiocqhttp
- [ ] astrbot_version 为 >4.26.0
- [ ] echo 脚手架完全移除
- [ ] README 与公共 API 同步
- [ ] pytest / ruff / compileall / loader lifecycle 全部通过
