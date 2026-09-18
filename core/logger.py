from __future__ import annotations

from typing import Any

from astrbot.api import logger as _astrbot_logger


class FileListenerLogger:
    """为插件日志统一添加可检索前缀。"""

    PREFIX = "[astrbot_plugin_file_listener] "

    @staticmethod
    def _with_stacklevel(kwargs: dict[str, Any]) -> dict[str, Any]:
        """保留日志的真实调用位置。"""
        copied = dict(kwargs)
        copied.setdefault("stacklevel", 2)
        return copied

    def _message(self, message: object) -> str:
        return f"{self.PREFIX}{message}"

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.debug(
            self._message(message), *args, **self._with_stacklevel(kwargs)
        )

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.info(
            self._message(message), *args, **self._with_stacklevel(kwargs)
        )

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.warning(
            self._message(message), *args, **self._with_stacklevel(kwargs)
        )

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.error(
            self._message(message), *args, **self._with_stacklevel(kwargs)
        )

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.exception(
            self._message(message), *args, **self._with_stacklevel(kwargs)
        )


logger = FileListenerLogger()
