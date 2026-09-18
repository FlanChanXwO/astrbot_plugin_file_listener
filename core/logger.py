from __future__ import annotations

from typing import Any

from astrbot.api import logger as _astrbot_logger


class FileListenerLogger:
    """统一转发插件日志到 AstrBot logger。"""

    @staticmethod
    def _with_stacklevel(kwargs: dict[str, Any]) -> dict[str, Any]:
        """保留日志的真实调用位置。"""
        copied = dict(kwargs)
        copied.setdefault("stacklevel", 2)
        return copied

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.debug(message, *args, **self._with_stacklevel(kwargs))

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.info(message, *args, **self._with_stacklevel(kwargs))

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.warning(message, *args, **self._with_stacklevel(kwargs))

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.error(message, *args, **self._with_stacklevel(kwargs))

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        _astrbot_logger.exception(message, *args, **self._with_stacklevel(kwargs))


logger = FileListenerLogger()
