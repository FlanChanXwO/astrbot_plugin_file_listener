from __future__ import annotations

import pytest

from astrbot_plugin_file_listener.main import FileListenerPlugin


class FakeEvent:
    """只实现 echo 命令公开依赖的最小事件接口。"""

    def plain_result(self, text: str) -> str:
        return text


@pytest.mark.asyncio
async def test_echo_returns_message_unchanged() -> None:
    plugin = object.__new__(FileListenerPlugin)

    results = [
        result
        async for result in plugin.echo(FakeEvent(), "file listener ready")
    ]

    assert results == ["file listener ready"]
