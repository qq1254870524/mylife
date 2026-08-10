# 更新日志

## v1.0.1 - 2026-08-11

- 修复 Windows CMD 启动器使用纯 LF 换行时被 `cmd.exe` 错位解析的问题。
- 启动器改为明确保存 CRLF 字节，并通过 `.gitattributes` 禁止 Git/Release 包再次把 `.cmd` 规范化成 LF。
- 环境变量赋值增加引号，失败时先保存真实错误码再暂停退出。
- Python 功能模块和 `设计思路.txt` 均未改动。

## v1.0.0 - 2026-08-11

- 完成正式模块化架构，每个稳定功能独立维护。
- 保持已有 `turnstile_harvester.py` 原字节不变。
- 新增 GUI、开始/停止、线程数量、小窗口/无头、输入文件和输出目录。
- 新增可动态增删的 SOCKS5 代理池、启动连通性检查、刷新后 10 秒复查和 3 分钟刷新冷却。
- 新增带认证 SOCKS5 到 Chromium 的本地 `pproxy` 桥。
- 新增代理出口 IP 对应时区、语言和地理位置配置。
- 新增 TXT/CSV/XLSX/XLSM 自适应输入与任意附加列前置保留。
- 新增 SQLite WAL、多个浏览器线程、一个数据库写入线程和实时 UTF-8 BOM CSV。
- 新增姓名+地点优先、姓名回退、结果分页、逐详情生日采集。
- 新增正常结束后一次性原子重建输入文件；处理中不删 XLSX 行。
- 新增浏览器复用、每人结束清理、Cloudflare 连续失败刷新 IP 并新建浏览器。
- 新增自动依赖安装与唯一 CMD 启动器。
- 新增离线单元/集成测试、验证记录、差异文件和回滚脚本。
- 只读参考 `E:\truepeoplesearch` 的持续 Turnstile 控件轮询与 `E:\txfgsales` 的 Patchright 启动方式：挑战期间每 450ms 重查晚加载控件；Patchright“无头”改为真实 headful Chrome + Win32 隐藏，避免真 headless 无法取得 Managed Turnstile token。
- 新增 Chromium 本地网络访问检查兼容参数；代理地理位置在 Cloudflare 放行后才应用，减少启动阶段 CDP 配置对无感验证的影响。
- MyLife 真实小窗口全链路验证已通过：搜索页挑战放行、1 条搜索结果、逐详情访问、实时 CSV、SQLite 完成状态、批次结束输入重建均成功。
