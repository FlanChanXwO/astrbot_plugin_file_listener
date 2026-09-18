# astrbot_plugin_file_listener

AstrBot 文件监听插件。

当前仓库只完成插件基础架构初始化，尚未实现文件监听业务。

## 当前能力

- AstrBot 原生插件生命周期入口。
- 统一的插件日志器。
- 通用路径工具。
- /echo <text> 测试命令，用于验证命令链路。

## 开发验证

在 AstrBot 环境中运行 tests 下的 pytest 测试。

涉及插件入口或生命周期时，还需要使用 astrbot_plugin_dna 中的生命周期检查器进行真实加载、初始化、卸载和资源清理验证。
