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
