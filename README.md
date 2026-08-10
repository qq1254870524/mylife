# MyLife 数据采集正式版

版本：`1.0.0`

## 启动

双击唯一启动器：

```text
启动MyLife正式版.cmd
```

启动器会先检查依赖；已经安装的依赖全部跳过，缺少的依赖才会自动下载。GUI 支持：

- `TXT / CSV / XLSX / XLSM` 自动识别；
- 输入任意列顺序，优先识别名、姓、城市、州、邮编、完整姓名和地址；
- 原输入列原样位于结果前部，MyLife 结果字段追加在后部；
- 多浏览器处理线程 + 单独 SQLite 写入线程；
- SQLite `WAL` 断点状态和 UTF-8 BOM CSV 实时落盘；
- “姓名+城市州邮编”优先，找不到时自动回退“姓名”；
- 遍历搜索结果分页，再逐条进入详情采集生日；
- 小窗口/无头两种浏览器模式；
- 动态 SOCKS5 代理池、连通性检查、IP 地理时区/语言同步、3 分钟刷新冷却；
- 停止时结束所有工作线程和浏览器，并删除浏览器缓存；
- 正常批次结束后才一次性原子重建输入文件，不在处理中删除 XLSX 行。

## 输入建议

最明确的表头组合：

```text
first_name,last_name,city,state,zip
```

也支持中文表头，例如：

```text
名,姓,城市,州,邮编,任意附加资料
```

只有一列时，列名可为 `query`、`name` 或 `姓名`，内容使用至少两个英文姓名词。邮箱或手机号不能推导出 MyLife 姓名时会输出“输入无效”，不会误当姓名搜索。

## 代理格式

每行一套：

```text
host:port:user:password|https://刷新链接
```

GUI 可用 `＋` 添加，用 `－` 删除。首次启动会从本机 `设计思路.txt` 自动读取符合格式的代理行；GUI 保存项位于 `.mylife_gui_settings.json`，该文件不会进入 Git 仓库或 Release。

Chromium 不支持带账号密码的 SOCKS5 直连，`socks_bridge.py` 会为每套代理自动创建仅监听 `127.0.0.1` 的本地 SOCKS5 桥，再把浏览器固定绑定到对应代理。域名请求会原样交给远端代理解析。

Patchright 的真 headless 模式会影响 Managed Turnstile token 签发，因此 GUI 的“无头”模式内部仍运行真实 Chrome，再只隐藏本次会话新增的窗口；不会触碰用户已有 Chrome 窗口。“小窗口”则保留可见浏览器便于实时观察。

## 输出

- 实时 CSV：`输出目录/<输入文件名>_MyLife结果.csv`
- SQLite：`输出目录/.mylife_runtime/state.sqlite3`
- 日志：`输出目录/logs/run_YYYYMMDD_HHMMSS.log`

CSV 每写一行都会 `flush + fsync`。输出文件首列保持为输入首列；批次正常结束后，输入文件中首列已经出现在输出首列的整行会被一次性删除。技术失败且没有明确输出的行会保留，供下次断点继续。

## 模块

| 文件 | 职责 |
|---|---|
| `main.py` | 正式入口 |
| `bootstrap.py` | 缺失依赖检查与安装 |
| `gui.py` | GUI、代理增删、实时监控 |
| `controller.py` | 生命周期、线程、断点、批次收尾 |
| `browser_worker.py` | Patchright 浏览器、人类式节奏、搜索/详情流程 |
| `cloudflare_handler.py` | 持续轮询晚加载 Turnstile 控件并执行真实鼠标点击 |
| `turnstile_harvester.py` | 已有 Cloudflare/Turnstile 模块（正式版未改） |
| `proxy_pool.py` | 代理解析、检查、刷新、地理信息、本地桥 |
| `socks_bridge.py` | 远端认证 SOCKS5 的本地无认证桥 |
| `input_loader.py` | TXT/CSV/XLSX 自动识别 |
| `mylife_parser.py` | 搜索页、分页、详情和生日解析 |
| `database.py` | SQLite WAL 与单写线程 |
| `output_writer.py` | UTF-8 BOM CSV 实时输出 |
| `source_rewriter.py` | 批次结束后一次性重建输入 |
| `models.py` | 数据模型 |

## 验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m unittest discover -s tests -v
python -B main.py --check
```
