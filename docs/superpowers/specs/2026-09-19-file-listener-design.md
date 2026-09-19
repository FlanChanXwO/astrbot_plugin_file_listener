# astrbot_plugin_file_listener 设计规格

日期：2026-09-19

## 1. 目标

`astrbot_plugin_file_listener` 是一个面向 AstrBot 的通用文件事件监听基础设施插件。首期只支持 Telegram 与 OneBot，负责监听普通群聊文件、私聊文件、平台可用的文件上传通知，以及合并转发中的文件，并将这些平台事件统一规范化为稳定的文件事件模型。

插件同时提供公共监听注册 API。其他 AstrBot 插件可以注册多个异步 callback，每个 callback 最多绑定一条自己的 FilterChain。一个源事件无论包含多少文件，对同一个 callback 最多触发一次。

插件默认提供“检测到文件后立即回复下载链接”的内置监听行为，并允许通过插件配置关闭或调整回复策略。

## 2. 非目标

首期明确不实现以下能力：

- Telegram / OneBot 之外的平台适配；
- callback 自动重试、filter 自动重试；
- callback timeout；
- detached background task、后台 worker 或任务队列；
- callback 注册持久化；
- File Listener 重载后的 callback 自动迁移；
- 死信队列；
- 文件下载代理或链接脱敏服务；
- 通用事件总线框架。

保持 KISS/YAGNI。新增平台或更复杂调度能力应在出现真实需求后再扩展。

## 3. 发布元数据约束

`metadata.yaml` 只声明当前真实支持的平台：

```yaml
support_platforms:
  - telegram
  - aiocqhttp

astrbot_version: ">4.26.0"
```

说明：产品文案和文档使用“Telegram / OneBot”，但 AstrBot 的 `support_platforms` 必须使用 `ADAPTER_NAME_2_TYPE` 的实际 key，因此 OneBot 对应 `aiocqhttp`。其他平台在没有正式适配前不写入 metadata。

## 4. 总体架构

采用 Binding-centric Pipeline：

```text
Telegram / OneBot 原始事件
        ↓
Platform Adapter
        ↓
FileEventBatch
        ↓
Source Gate
        ↓
Deduplicator
        ↓
Binding Dispatcher
        ├── DirectLink Binding
        │     └── FilterChain → callback
        ├── Callback Binding A
        │     └── FilterChain A → callback A
        └── Callback Binding B
              └── FilterChain B → callback B
```

职责边界：

- 平台差异止于 Platform Adapter；
- 一个源事件止于一个 `FileEventBatch`；
- 每个 binding 的过滤状态止于自己的 `FilterContext`；
- 业务副作用和最终处理止于 `CallbackBinding`。

不同 binding 默认并行执行，但每个 binding 内部仍按“FilterChain → callback”顺序执行。

## 5. 核心数据模型

### 5.1 FileEvent

`FileEvent` 表示单个文件，提供平台无关的稳定字段：

```text
FileEvent
├─ file_name: str
├─ file_url: str | None
├─ file_id: str | None
├─ file_size: int | None
├─ platform: str
├─ chat_type: "group" | "private"
├─ source_type: str
├─ chat_id: str | None
├─ sender_id: str | None
└─ raw_file: object | None
```

`file_url` 允许为空。拿不到 URL 不等于文件事件失败，后续 callback 仍应收到该文件。

`source_type` 由平台 adapter 提供稳定字符串，不做跨平台统一死枚举。例如 OneBot 可以产生 `message`、`private_file`、`group_upload_notice`、`merged_forward`；Telegram 可以产生 `message`、`forwarded`。

平台私有信息不继续扩散到核心模型；特殊需求通过 `raw_file` 或 batch 的 `raw_event` 读取。

### 5.2 FileEventBatch

一个源事件只生成一个 batch：

```text
FileEventBatch
├─ files: tuple[FileEvent, ...]
├─ raw_event: AstrMessageEvent
├─ platform: str
├─ source_type: str
└─ event_id: str | None
```

普通单文件消息的 `files` 长度为 1。合并转发可能包含多个文件，但仍然只形成一个 `FileEventBatch`。

`files` 使用不可变 tuple，避免 callback 无意修改原始 batch。

### 5.3 FilterContext

每个 `CallbackBinding` 执行时创建独立上下文：

```text
FilterContext
├─ original_batch: FileEventBatch
├─ current_files: list[FileEvent]
├─ continue_chain: bool
└─ reason: str | None
```

整条 FilterChain 必须始终传递同一个 `FilterContext` 实例：

```python
returned_context is input_context
```

filter 可以修改 `current_files`、`continue_chain`、`reason`，但不得替换 `original_batch`，也不得对 `raw_event` 留下持久修改。

过滤结束后：

- `current_files` 非空：构造新的只读 `FileEventBatch` 传给 callback；
- `current_files` 为空：该 callback 不触发。

### 5.4 CallbackBinding

```text
CallbackBinding
├─ callback: async callable
└─ filter_chain: FilterChain | None
```

一个 callback 最多绑定一条 FilterChain。同一个 listener 生命周期内，同一个 callback 不允许重复注册。

### 5.5 ListenerOptions

`ListenerOptions` 是插件生命周期级只读配置快照，只在插件重载后通过新实例发生变化：

```text
ListenerOptions
├─ parallel: bool = True
├─ send_direct_link: bool = True
├─ file_reply_mode: "smart" | "aggregate" | "separate"
├─ forward_max_depth: int
├─ dedupe_enabled: bool
├─ dedupe_window_seconds: float
├─ monitor_sources: ...
└─ direct_link_template: str
```

## 6. 公共注册 API

其他插件通过 AstrBot `Context.get_registered_star(...)` 获取当前 File Listener 插件实例，再通过明确的公共 accessor 取得 listener，例如：

```python
metadata = context.get_registered_star("astrbot_plugin_file_listener")
plugin = metadata.star_cls
listener = plugin.get_file_listener()
```

不维护额外的进程级 service registry，也不鼓励调用方直接 import 模块级全局实例。

### 6.1 Callback 签名

只支持 async callback：

```python
async def callback(
    batch: FileEventBatch,
    options: ListenerOptions,
) -> None: ...
```

规则：

- 同一源事件对同一个 callback 最多调用一次；
- callback 接收自己 FilterChain 处理后的 batch；
- callback 异常只影响当前 binding，不影响其他 binding。

### 6.2 Filter 签名

```python
async def file_filter(context: FilterContext) -> FilterContext:
    ...
    return context
```

filter 允许产生外部副作用，但必须保证原始事件对象没有残留修改。发送消息、记录日志等外部副作用不要求回滚。

filter 异常时：

- 当前 FilterChain 立即中止；
- 当前 callback 不触发；
- 其他 binding 继续执行。

### 6.3 FilterChain 排序

filter 使用 `priority` 排序：

- 数值越小越先执行；
- 相同 priority 按注册顺序稳定执行；
- 不支持 `before/after` 拓扑依赖。

### 6.4 批量注册

一次 `register()` 可以传入多个 binding：

```python
handle = listener.register(
    bindings=[binding_a, binding_b, binding_c],
)
```

注册必须具有原子性。只要 bindings 中存在重复 callback、非法 filter 或其他注册错误，整批都不注册。

返回 `RegistrationHandle`，支持：

```python
handle.unregister()
```

`unregister()` 必须幂等。

## 7. 内置 DirectLink Binding

自动回复文件直链不使用独立旁路，而是 File Listener 初始化时注册一个内置 `CallbackBinding`。

该 binding 的 FilterChain 第 0 位固定为 `SendDirectLinkFilter`：

```text
DirectLinkBinding
└─ FilterChain
   ├─ 0. SendDirectLinkFilter
   └─ 后续可选 filters
```

`SendDirectLinkFilter` 是系统固定前置 filter，不参与普通 `priority` 排序；无论第三方或后续内置 filter 使用什么 priority，都不能插到它前面。

发送副作用只发生在 `SendDirectLinkFilter`。DirectLink binding 的终端 callback 不再重复发送文件链接，只作为统一 Binding Pipeline 的终点存在，因此不会出现“filter 发一次、callback 再发一次”的重复行为。

只要 `send_direct_link=true`，检测到文件后就立即执行发送行为。后续 filter 不能撤销已经发送的消息。

`send_direct_link=false` 时不发送直链，但第三方 bindings 继续正常工作。

## 8. 平台事件采集

### 8.1 公共路径优先

优先通过 AstrBot 公共事件 API 和已规范化的 `File` 消息组件读取普通文件消息。

只有 AstrBot 公共抽象未覆盖的能力，才允许在平台 adapter 边界读取平台原始事件或调用平台专用动作，并在代码附近说明公开 API 无法覆盖的原因。

不因为内部路径更直接就依赖 `astrbot.core`。

### 8.2 Telegram

首期支持来源：

```text
telegram
├─ message
└─ forwarded
```

- 普通群聊 / 私聊 document 走 AstrBot 已规范化的 `File` 组件；
- 带转发来源的 document 标记为 `forwarded`；
- Telegram 不实现 QQ 风格的递归合并转发解析。

已知安全边界：当前设计按用户确认直接发送平台提供的原始 URL，不做代理或脱敏。Telegram Bot API 文件下载 URL 可能包含 bot token，并且链接是临时的。这是明确接受的设计风险，后续如需安全公开链接应单独设计代理能力。

### 8.3 OneBot

首期支持来源：

```text
onebot
├─ message
├─ private_file
├─ group_upload_notice
└─ merged_forward
```

其中 `message` 表示普通群文件消息，`private_file` 表示普通私聊文件消息。两者都优先消费 AstrBot 已规范化的 `File` component，source classifier 必须保证二者互斥。

`group_upload_notice` 当前 AstrBot aiocqhttp adapter 未统一转换为 File component，因此 OneBot adapter 需要在平台边界识别原始 notice，并提取 `file.id`、`file.name`、`file.size` 等字段。若 notice 本身没有可直接发送的 URL，但具备可解析的 `file_id` / 群上下文，则 adapter 应尝试通过 OneBot 文件 URL 能力补齐 `file_url`；解析失败时仍保留 `FileEvent(file_url=None)`，不丢弃文件事件。

### 8.4 OneBot 合并转发

收到 forward segment 后，根据其 forward id 调用平台能力获取节点内容，并递归遍历嵌套 forward：

```text
forward segment
    ↓
get_forward_msg
    ↓
遍历 node.content
    ↓
提取 File
    ↓
遇到嵌套 forward 时继续递归
    ↓
达到 forward_max_depth 后停止继续展开
```

已经提取出的文件不会因为更深层节点失败而丢失。

合并转发节点中的文件如果只提供 `file_id` 而没有 URL，也应在 adapter 边界尽力使用当前会话可用的 OneBot 文件 URL 能力补齐；不为了补齐 URL 下载完整文件。

无论合并转发中有多少文件，都只形成一个 `FileEventBatch`。

## 9. Source Gate

监控来源按平台配置，默认全部启用。

来源检查应尽可能早执行：先做廉价 source 分类，再检查 `monitor_sources`，只有启用后才进行可能需要网络调用的解析。

例如关闭 `merged_forward` 后，不应再调用 `get_forward_msg`。

用户配置使用产品语义 `onebot`，adapter 内部再映射 AstrBot `aiocqhttp`。

## 10. 去重设计

去重发生在 adapter 生成 `FileEventBatch` 之后、任何 binding 分发之前。

只做短时事件级去重，不做长期历史去重，缓存仅存在于当前插件生命周期内且不落盘。

### 10.1 身份优先级

每个文件按可靠程度选择 canonical identity：

```text
1. platform_instance + chat_id + file_id
2. platform_instance + chat_id + file_url
3. platform_instance + chat_id + file_name + file_size
4. 无法形成可靠身份 → 不参与跨事件去重
```

必须使用平台实例 ID，而不是只使用 `telegram` / `aiocqhttp`，因为同一个 AstrBot 实例可能同时运行多个相同类型的 bot adapter。

`sender_id` 不作为主要身份字段，避免 message / notice 字段差异导致 mirror 事件无法去重。

### 10.2 Batch 指纹

多文件 batch 根据稳定归一化后的文件身份集合计算 fingerprint。文件顺序差异不能导致 fingerprint 变化。

### 10.3 dedupe_family

不能仅因为两个事件包含同一个文件就认为它们是重复操作。去重还必须区分内部 `dedupe_family`。

典型 mirror：

```text
OneBot message ↔ group_upload_notice
```

典型非 mirror：

```text
普通上传 ↔ merged_forward
```

因此普通上传后再转发同一文件仍应触发新的文件事件。

当 canonical identity 只能退化到 `file_name + file_size` 时，跨来源去重只用于已知 mirror source 之间的比较，不用这个弱身份去压制同一 source 内连续发生的两个事件，以降低同名同大小文件被误判为重复的风险。

### 10.4 两级判断

```text
相同可信 event_id
    → 明确重复

否则
    ↓
相同 dedupe_family
+ 相同 canonical batch fingerprint
+ 位于 dedupe window 内
    → 跨来源重复
```

缓存只需要内存中的 `fingerprint → last_seen_timestamp`，事件处理时顺便淘汰过期项，不创建后台清理任务。

`dedupe_window_seconds` 保留为配置项。最终默认值不在设计阶段拍脑袋决定；实现阶段先实测 OneBot/NapCat 的 message 与 group_upload_notice 正常到达间隔，再选择能够覆盖正常抖动的最小合理值并写入 schema。

## 11. 插件配置

配置分为四组：

```text
runtime
monitor_sources
direct_link
dedupe
```

### 11.1 runtime

```yaml
runtime:
  parallel: true
  forward_max_depth: 3
```

`parallel=true` 时并行执行完整 `CallbackBinding` 流水线，而不是只并行 callback 函数。

达到 `forward_max_depth` 后保留已提取文件，只停止继续深入。

### 11.2 monitor_sources

按平台分别配置，默认全部启用：

```yaml
monitor_sources:
  telegram:
    - message
    - forwarded

  onebot:
    - message
    - private_file
    - group_upload_notice
    - merged_forward
```

Dashboard 优先使用 AstrBot 已有的 `list + options + checkbox` 形式呈现。

source classifier 必须保证一个实际事件得到唯一来源分类，不能因为 `message` 与 `private_file` 同时开启就重复解析同一事件。

### 11.3 direct_link

```yaml
direct_link:
  enabled: true
  reply_mode: smart
  template: |-
    文件名：{file_name}
    大小：{file_size}
    链接：{file_url}
```

`reply_mode`：

- `smart`：默认。普通单文件单条回复；多文件 / 合并转发聚合回复；
- `aggregate`：同一 batch 强制聚合；
- `separate`：每个文件单独回复。

模板只有一份，单文件和多文件使用同一模板。多文件聚合时逐文件渲染同一模板再拼接。

模板配置字段使用：

```text
type: string
editor_mode: true
editor_language: plaintext
```

支持占位符：

```text
{file_name}
{file_size}
{file_url}
```

当 `file_size` 不存在时，包含 `{file_size}` 的整行删除，其余换行结构保持不变。

未知占位符属于配置错误：启动时 warning，并对该字段回退默认模板，而不是在事件处理中抛异常。

文件名和 URL 是默认直链输出的核心字段；当某个文件 `file_url=None` 时，DirectLink binding 跳过该文件，但第三方 callback 仍可收到它。

### 11.4 dedupe

```yaml
dedupe:
  enabled: true
  window_seconds: <实现阶段根据实测确定默认值>
```

## 12. 执行与错误隔离

### 12.1 并行模式

默认 `parallel=true`：

```text
Binding A ─┐
Binding B ─┼─ concurrently await
Binding C ─┘
```

每个 binding 都有独立 FilterContext。

任何一个 binding 的异常都不能取消 sibling binding。

### 12.2 串行模式

`parallel=false`：

```text
Binding A
  ↓
Binding B
  ↓
Binding C
```

某个 binding 失败后仍继续执行后续 binding。

### 12.3 Adapter 错误

平台解析错误分两类：

- 可部分恢复：保留已成功提取文件，失败节点记录 warning；
- 完全无法形成文件事件：返回 `None`，不进入 Dispatcher。

拿不到 URL 不算解析失败。

## 13. 生命周期

### 13.1 初始化

初始化顺序：

```text
读取配置
  ↓
构造 ListenerOptions
  ↓
创建 Telegram / OneBot adapters
  ↓
创建 Deduplicator
  ↓
创建 Listener / Binding Registry
  ↓
注册内置 DirectLink Binding
  ↓
开始接收事件
```

局部可恢复配置错误使用 warning + 字段默认值；核心配置无法解析时启动失败。

### 13.2 事件执行生命周期

不创建 detached background task。

即使 `parallel=true`，也只是在当前事件生命周期中并发执行多个 binding，并等待本次所有 binding 结束后再结束当前文件事件处理。

### 13.3 卸载

`terminate()`：

```text
停止接受新事件
  ↓
清空 Binding Registry
  ↓
使所有 RegistrationHandle 失效
  ↓
清空 dedupe 内存状态
  ↓
释放 adapter 临时状态
```

File Listener 配置重载等价于插件完整卸载再加载，因此不保留旧 callback 注册。第三方插件需要感知 File Listener 重载并重新获取当前实例、重新注册 callback。

File Listener 首期不额外提供跨插件重载通知总线。也就是说，第三方插件如果需要在 File Listener 被单独重载后自动恢复监听，必须通过其自身可用的生命周期/重绑定机制重新查询当前实例；旧 `RegistrationHandle` 不会自动指向新实例。

## 14. 测试策略

测试只覆盖公共行为、关键集成边界和高风险平台路径，不为每个 helper、dataclass、getter 单独建立组件级测试。

代码改动遵守 Red → Green → Refactor。

### 14.1 Listener / Binding 主流程

覆盖：

- batch 内多个文件时，同一 callback 只调用一次；
- 多个 callback 都能收到同一源事件；
- 重复 callback 注册被拒绝；
- 批量 register 原子性；
- `RegistrationHandle.unregister()` 幂等；
- parallel / serial 的执行语义；
- callback 异常隔离。

### 14.2 FilterChain 公共契约

覆盖：

- 每层返回同一个 `FilterContext` 实例；
- `original_batch` 不被修改；
- binding 间上下文隔离；
- 部分过滤后的 callback 输入；
- `current_files=[]` 时 callback 不触发；
- filter 异常只终止对应 binding；
- priority 与同优先级稳定注册顺序。

### 14.3 平台适配契约

优先使用真实事件结构 fixture，不对每个内部函数做细粒度 mock。

覆盖：

- Telegram 普通 document；
- Telegram forwarded document；
- OneBot 普通群 / 私聊 File component；
- OneBot group_upload_notice；
- OneBot 合并转发多个文件仍只生成一个 batch；
- 嵌套 forward 遵守最大深度；
- 深层失败不丢失已提取文件；
- 关闭某个 monitor source 后不发起对应专用解析请求。

### 14.4 直链与去重

覆盖：

- 默认模板渲染；
- 无 file_size 时删除整行；
- 多文件重复使用同一模板；
- smart / aggregate / separate；
- `send_direct_link=false`；
- message + group_upload_notice mirror 去重；
- 去重窗口过后同文件可再次触发；
- merged_forward 与普通上传不会因文件相同误去重。

### 14.5 AstrBot 生命周期集成

使用真实 AstrBot plugin loader 验证：

```text
load → initialize → handler/register → terminate
```

同时验证卸载后 registry、dedupe、registration handles 清理。

现有 DNA 插件的完整 lifecycle checker 额外要求 LLM tools / Web APIs，不适用于此插件。不得为了通过这个专用检查器伪造无关能力；应使用适用于普通插件的生命周期验证路径。

## 15. 现有脚手架收尾

当前初始化阶段的 `echo` 命令只是脚手架。正式功能完成后应删除 `echo` 命令及对应无业务意义测试，而不是长期保留。

已删除的 `tests/test_logger.py` 不恢复。日志包装这类微型组件不单独建立测试，继续通过架构规则与真实业务路径验证。

## 16. 验收标准

首期设计完成后的功能验收标准：

1. Telegram 与 OneBot 普通文件事件能够统一形成 `FileEventBatch`；
2. OneBot 群文件上传 notice 与合并转发可按配置启停；
3. 合并转发多文件对同一 callback 只触发一次；
4. 第三方插件能够通过 AstrBot Context 获取 listener 并注册多个 `CallbackBinding`；
5. 每个 callback 最多绑定一条 FilterChain，filter 按 priority 稳定执行；
6. filter 不对原始 AstrBot 事件留下持久修改；
7. callback / filter 故障隔离，不拖垮其他 binding；
8. 默认并行执行 binding；
9. DirectLink 默认启用，模板、回复模式和来源均可配置；
10. 多来源 mirror 事件不会重复发送直链或重复触发 callback；
11. 插件卸载后无遗留 callback registry、dedupe 状态或 detached task；
12. `metadata.yaml` 只声明 `telegram`、`aiocqhttp`，并要求 AstrBot `>4.26.0`；
13. 实现优先依赖 `astrbot.api` 公共能力，只有公开 API 确实不足的平台边界才允许使用底层能力并注明原因。
