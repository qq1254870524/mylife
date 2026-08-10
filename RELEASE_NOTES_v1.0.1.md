# MyLife v1.0.1 启动器热修复

- 修复 `启动MyLife正式版.cmd` 因纯 LF 换行导致 Windows `cmd.exe` 把命令截断的问题。
- 启动器现在固定使用 CRLF，Release ZIP 中也保留 CRLF。
- Python 功能模块、Cloudflare 处理、代理池、数据库和输入输出逻辑均未修改。
- 已通过批处理真实解析测试、18 项单元/集成测试和 `main.py --check`。
