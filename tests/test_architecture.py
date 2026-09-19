from __future__ import annotations

import ast
from pathlib import Path


def test_python_sources_only_use_public_astrbot_api() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for path in plugin_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "astrbot" or (
                        alias.name.startswith("astrbot.")
                        and not alias.name.startswith("astrbot.api")
                    ):
                        violations.append(
                            f"{path.relative_to(plugin_root)}:{node.lineno}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module
                if module and (
                    module == "astrbot"
                    or (
                        module.startswith("astrbot.")
                        and not module.startswith("astrbot.api")
                    )
                ):
                    violations.append(
                        f"{path.relative_to(plugin_root)}:{node.lineno}"
                    )

    assert violations == []


def test_file_event_handler_does_not_subscribe_to_all_message_types() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    tree = ast.parse((plugin_root / "main.py").read_text(encoding="utf-8"))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FileListenerPlugin"
        for node in node.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_file_event"
    )
    decorator = next(
        item
        for item in handler.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "event_message_type"
    )
    expression = ast.unparse(decorator.args[0])

    assert expression == (
        "filter.EventMessageType.GROUP_MESSAGE | "
        "filter.EventMessageType.PRIVATE_MESSAGE"
    )


def test_file_event_handler_only_subscribes_to_supported_platforms() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    tree = ast.parse((plugin_root / "main.py").read_text(encoding="utf-8"))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FileListenerPlugin"
        for node in node.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_file_event"
    )
    decorator = next(
        item
        for item in handler.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "platform_adapter_type"
    )
    expression = ast.unparse(decorator.args[0])

    assert expression == (
        "filter.PlatformAdapterType.AIOCQHTTP | "
        "filter.PlatformAdapterType.TELEGRAM"
    )
