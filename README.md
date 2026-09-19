# astrbot_plugin_file_listener

AstrBot 文件事件监听基础设施插件。首期支持 **Telegram** 与 **OneBot（aiocqhttp）**，把不同平台的文件上传事件统一成 `FileEventBatch`，并通过独立的 FilterChain + callback binding 分发给其它插件。

## 支持范围

Telegram：

- 普通群聊 / 私聊文件消息
- 转发文件消息

OneBot：

- 普通群文件消息
- 私聊文件消息
- `group_upload` 群文件上传通知
- 合并转发中的文件，支持可配置递归深度

其它平台暂未声明支持。

## 默认行为

插件默认开启 DirectLink：检测到文件后先通过内置 `FileLinkValidationFilter` 校验链接，再在当前会话发送平台提供的原始下载 URL。

默认模板：

```text
文件名：{file_name}
大小：{file_size}
链接：{file_url}
```

`{file_size}` 会自动按 1024 进制格式化为 `B / KB / MB / GB / TB / PB`；原始 callback 数据中的 `FileEvent.file_size` 仍保持字节整数。当平台无法可靠提供文件大小时，会删除包含 `{file_size}` 的整行。无法获得 URL 的文件仍然会进入 callback，只是不会参与 DirectLink 回复。

DirectLink 链接校验使用 `GET` + `Range: bytes=0-0`，只探测首字节，不完整下载文件。明确返回 `404` 等客户端错误的链接会从 DirectLink 自己的 `current_files` 中移除；timeout、DNS/TLS/连接失败、`5xx`、`429` 等无法可靠判断的情况采用 **fail-open**，保留链接并继续发送。该过滤只影响 DirectLink 自己的 FilterContext，不会删除第三方 callback 收到的原始 `FileEvent`。

多文件回复模式：

- `smart`：默认；单文件单条，多文件聚合
- `aggregate`：同一源事件始终聚合
- `separate`：每个文件单独发送

## 配置

插件配置分为四组：

- `runtime.parallel`：默认 `true`，并行执行不同 CallbackBinding 的完整流水线
- `runtime.ignore_self_messages`：默认 `true`，忽略 Bot 自身文件消息；关闭后会处理平台实际上报的自身文件消息。OneBot/NapCat 需要对应 WebSocket 客户端同时开启 `reportSelfMessage=true`；Telegram Bot API 主动发送的消息通常不会作为 update 回送给 Bot
- `runtime.forward_max_depth`：OneBot 合并转发最大递归深度，默认 `3`
- `monitor_sources`：按 Telegram / OneBot 分别选择监听来源，默认全部启用
- `direct_link`：控制自动直链、回复模式和模板
- `dedupe`：控制短时镜像事件去重

`dedupe.window_seconds` 默认 `3.0` 秒，用于覆盖 OneBot 同一次上传可能同时出现 `message` 与 `group_upload_notice` 的情况。该值是保守默认值，可按实际协议端行为调整。

## 第三方插件注册 callback

File Listener 不提供进程级全局 service registry。第三方插件通过 AstrBot `Context` 获取当前插件实例，再取得 listener：

```python
metadata = context.get_registered_star("astrbot_plugin_file_listener")
if metadata is None or metadata.star_cls is None:
    raise RuntimeError("astrbot_plugin_file_listener 未加载")
listener = metadata.star_cls.get_file_listener()
```

注册示例：

```python
from data.plugins.astrbot_plugin_file_listener.core import (
    CallbackBinding,
    FilterChain,
    FilterSpec,
)


async def only_zip(context):
    context.current_files[:] = [
        file for file in context.current_files if file.file_name.endswith(".zip")
    ]
    return context


async def handle_files(batch, options):
    for file in batch.files:
        print(file.file_name, file.file_url)


chain = FilterChain([FilterSpec(callback=only_zip, priority=100)])
handle = listener.register([CallbackBinding(callback=handle_files, filter_chain=chain)])
```

每个 callback 最多绑定一条 FilterChain，但一条 chain 可以包含多个 filter。每个源事件对同一个 callback 最多调用一次；合并转发即使包含多个文件，也只产生一个 `FileEventBatch`。

第三方插件卸载时应调用：

```python
handle.unregister()
```

`unregister()` 是幂等的。

### 重载语义

AstrBot 重载 File Listener 配置时会完整卸载并重新创建插件实例，因此旧 listener、旧 `RegistrationHandle` 和旧 callback registry 都不会迁移到新实例。需要持续监听的第三方插件必须重新查询当前 File Listener 实例并重新注册。

## FilterChain 契约

filter 必须是 async callable：

```python
async def file_filter(context):
    ...
    return context
```

每一层必须返回传入的**同一个** `FilterContext` 实例。filter 可以修改自己的 `current_files`、`continue_chain` 和 `reason`，但不应对 `raw_event` 留下不可逆修改。

不同 CallbackBinding 使用独立 FilterContext。某个 filter 或 callback 抛异常只终止自己的 binding，不影响其它 binding。

内置 DirectLink 的系统链顺序为：

```text
FileLinkValidationFilter
→ SendDirectLinkFilter
→ 普通 priority filters
```

`FileLinkValidationFilter` 也从 `core` 公共 API 导出，可被第三方 binding 直接复用。

## Telegram 原始 URL 风险

当前设计按配置直接发送平台提供的原始 URL，不做代理或脱敏。Telegram Bot API 的文件下载 URL 可能包含 bot token，而且属于临时链接。**不要把这类 URL 当作安全、永久的公开下载地址。**

如果需要对外长期分享文件，应在后续版本单独增加受控代理/文件服务，而不是复用当前 DirectLink。

## 开发验证

在 AstrBot 项目环境中运行：

```bash
PYTHONPATH=.. /srv/AstrBot/.venv/bin/python -m pytest tests -q
/srv/AstrBot/.venv/bin/ruff check .
/srv/AstrBot/.venv/bin/python -m compileall .
```

插件生命周期变更还需要使用普通 AstrBot plugin loader 路径验证真实导入、初始化和卸载。不要为了通过其它插件的专用 checker 添加无关 LLM tool 或 Web API。
