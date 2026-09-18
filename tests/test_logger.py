from __future__ import annotations

import importlib

logger_module = importlib.import_module("astrbot_plugin_file_listener.core.logger")


class RecordingLogger:
    """记录转发给 AstrBot logger 的消息。"""

    def __init__(self) -> None:
        self.messages: list[object] = []

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.messages.append(message)


def test_logger_forwards_message_without_plugin_prefix(monkeypatch) -> None:
    backend = RecordingLogger()
    monkeypatch.setattr(logger_module, "_astrbot_logger", backend)

    logger_module.logger.info("插件初始化完成")

    assert backend.messages == ["插件初始化完成"]
