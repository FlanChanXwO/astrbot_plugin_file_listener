from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from .direct_link import DEFAULT_DIRECT_LINK_TEMPLATE, validate_direct_link_template
from .logger import logger
from .models import DEFAULT_MONITOR_SOURCES, ListenerOptions

# 保守默认值；部署方可根据实际 OneBot message/group_upload 到达间隔调整。
DEFAULT_DEDUPE_WINDOW_SECONDS = 3.0
_REPLY_MODES = {"smart", "aggregate", "separate"}


def _section(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key, {})
    if isinstance(value, Mapping):
        return value
    logger.warning("配置项 %s 应为 object，已回退默认值", key)
    return {}


def _bool_value(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    logger.warning("配置项 %s 应为 bool，已回退默认值", key)
    return default


def _source_set(
    section: Mapping[str, Any],
    key: str,
    allowed: frozenset[str],
) -> frozenset[str]:
    value = section.get(key)
    if value is None:
        return allowed
    if not isinstance(value, list | tuple | set | frozenset):
        logger.warning("监听来源 %s 应为 list，已回退默认值", key)
        return allowed
    requested = {str(item) for item in value}
    unknown = requested - allowed
    if unknown:
        logger.warning("监听来源 %s 包含未知值 %s，已忽略", key, sorted(unknown))
    return frozenset(requested & allowed)


def build_listener_options(config: Mapping[str, Any]) -> ListenerOptions:
    """把 AstrBot 插件配置转换为生命周期级只读 ListenerOptions。

    Args:
        config: AstrBotConfig 或普通 mapping。

    Returns:
        已完成局部容错和模板校验的 ListenerOptions。
    """
    runtime = _section(config, "runtime")
    monitor = _section(config, "monitor_sources")
    direct_link = _section(config, "direct_link")
    dedupe = _section(config, "dedupe")

    parallel = _bool_value(runtime, "parallel", True)
    ignore_self_messages = _bool_value(runtime, "ignore_self_messages", True)
    raw_depth = runtime.get("forward_max_depth", 3)
    if isinstance(raw_depth, bool) or not isinstance(raw_depth, int) or raw_depth <= 0:
        logger.warning("forward_max_depth 必须为正整数，已回退为 3")
        forward_max_depth = 3
    else:
        forward_max_depth = raw_depth

    telegram_sources = _source_set(
        monitor,
        "telegram",
        DEFAULT_MONITOR_SOURCES["telegram"],
    )
    onebot_sources = _source_set(
        monitor,
        "onebot",
        DEFAULT_MONITOR_SOURCES["onebot"],
    )
    monitor_sources = MappingProxyType(
        {"telegram": telegram_sources, "onebot": onebot_sources}
    )

    send_direct_link = _bool_value(direct_link, "enabled", True)
    raw_reply_mode = direct_link.get("reply_mode", "smart")
    if not isinstance(raw_reply_mode, str) or raw_reply_mode not in _REPLY_MODES:
        logger.warning("reply_mode 无效，已回退为 smart")
        reply_mode = "smart"
    else:
        reply_mode = raw_reply_mode

    raw_template = direct_link.get("template", DEFAULT_DIRECT_LINK_TEMPLATE)
    if not isinstance(raw_template, str):
        logger.warning("DirectLink template 应为字符串，已回退默认模板")
        template = DEFAULT_DIRECT_LINK_TEMPLATE
    else:
        try:
            validate_direct_link_template(raw_template)
        except ValueError as exc:
            logger.warning("DirectLink template 无效，已回退默认模板: %s", exc)
            template = DEFAULT_DIRECT_LINK_TEMPLATE
        else:
            template = raw_template

    dedupe_enabled = _bool_value(dedupe, "enabled", True)
    raw_window = dedupe.get("window_seconds", DEFAULT_DEDUPE_WINDOW_SECONDS)
    if isinstance(raw_window, bool):
        raw_window = -1
    try:
        dedupe_window = float(raw_window)
    except (TypeError, ValueError):
        dedupe_window = -1
    if dedupe_window <= 0:
        logger.warning(
            "dedupe.window_seconds 必须大于 0，已回退为 %.1f",
            DEFAULT_DEDUPE_WINDOW_SECONDS,
        )
        dedupe_window = DEFAULT_DEDUPE_WINDOW_SECONDS

    return ListenerOptions(
        parallel=parallel,
        ignore_self_messages=ignore_self_messages,
        send_direct_link=send_direct_link,
        file_reply_mode=reply_mode,
        forward_max_depth=forward_max_depth,
        dedupe_enabled=dedupe_enabled,
        dedupe_window_seconds=dedupe_window,
        monitor_sources=monitor_sources,
        direct_link_template=template,
    )
