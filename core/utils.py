from __future__ import annotations

from pathlib import Path


class PathUtils:
    """插件通用路径工具。"""

    @staticmethod
    def plugin_dir(module_file: str) -> Path:
        """返回插件源码根目录。

        Args:
            module_file: 调用模块的 __file__。

        Returns:
            规范化后的插件源码目录。
        """
        return Path(module_file).resolve().parent
