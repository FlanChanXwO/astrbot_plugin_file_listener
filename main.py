from __future__ import annotations

from dataclasses import dataclass

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core import PathUtils, logger


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """生命周期检查器使用的最小命令清单项。"""

    id: str


# astrbot_plugin_dna 的生命周期检查器会核对该投影与 commands.json。
COMMAND_REGISTRY = (CommandSpec(id="echo"),)


class FileListenerPlugin(Star):
    """群文件监听插件入口。当前仅提供 echo 初始化测试命令。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.base_dir = PathUtils.plugin_dir(__file__)

    async def initialize(self) -> None:
        """完成插件初始化。"""
        logger.info("插件初始化完成")

    async def terminate(self) -> None:
        """完成插件卸载。"""
        logger.info("插件已卸载")

    @filter.command("echo")
    async def echo(self, event: AstrMessageEvent, message: str = ""):
        """原样返回输入文本，用于验证插件命令链路。"""
        yield event.plain_result(message)
