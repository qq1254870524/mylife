# MyLife 数据采集正式版

版本：`1.1.5`

## 启动

双击唯一启动器：

```text
启动MyLife正式版.cmd
```

启动器会先检查依赖；已经安装的依赖全部跳过，缺少的依赖才会自动下载。GUI 支持：

- `TXT / CSV / XLSX / XLSM` 自动识别；
- 输入任意列顺序，识别名、姓、年龄、城市、州、邮编、完整姓名、当前地址、曾用地址、手机号、邮箱和关联资料；
- 自动识别无表头的“电话/SSN/已有生日/姓名/年龄/地址/城市/州/邮编/邮箱/电话列表/出生年/参考年龄”人员 XLSX，第一物理行按数据处理；
- 原输入列原样位于结果前部，MyLife 结果字段追加在后部；
- 多浏览器处理线程 + 单独 SQLite 写入线程；队尾暂时无待领任务但仍有在途任务时，空闲 worker 保持等待，重试重新入队后不会丢线程；
- GUI 新增“查询方式”：`HTTP接口` 使用浏览器初始化 Cloudflare 会话后，以每 worker 独立 HTTP 会话执行搜索和详情；`浏览器` 保留原 Patchright 全流程。
- SQLite `WAL` 断点状态和 UTF-8 BOM CSV 实时落盘；
- “姓名+城市州邮编”优先；任一搜索步骤的全部分页合计只有 1 人时直接确定；地点结果为空时自动回退“姓名”搜索，多候选且只有异龄候选时继续扩展并合并去重；
- 主姓名仍没有同龄候选时尝试经过噪声过滤的曾用名，过滤地址文字和无关人员姓名；
- 逐条进入详情采集年龄、生日、性别、星座，关闭可见弹层后使用保存的 GET 地址稳定返回搜索列表；
- 先限定同龄候选，再以原始查询手机号、公开完整电话、邮箱、当前/曾用地址、房产 JSON 地址、邮编、门牌号、城市州、去年龄后的亲属和关联人多信号评分；
- 无完全同龄候选时先检查经过姓名/身份阈值过滤的相差 1 岁候选，再回退全候选；当前地点与姓名仍无同龄目标时最多补搜 3 个曾用地点；
- 提取可见及 HTML 结构化完整生日/性别；星座可由完整生日确定，严格同身份重复档案可补充缺失字段；
- 只输出身份得分最高的一位，生日是否公开不会覆盖更强身份关系；
- 小窗口/无头两种浏览器模式；
- 动态 SOCKS5 代理池、连通性检查、IP 地理时区/语言同步、3 分钟刷新冷却；代理短暂失效时关闭错误浏览器但保持工作线程，恢复后以全新 profile 继续；
- 停止时结束所有工作线程和浏览器，恢复 Chromium AppContainer 缓存目录 ACL 后删除全部 profiles；底层包装后的取消统一保存为 `retry`，退回本次尝试次数，并在下次启动修复旧版不可领取的耗尽重试行；
- 正常批次结束后才一次性原子重建输入文件，不在处理中删除 XLSX 行。
- `health.json` 实时记录数据库、CSV 字段数量、线程、日志和浏览器诊断状态。
- 成功终态在清理 SQLite 断点后仍保持本批次总数、完成数和最终 CSV 字段计数。

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

无表头人员表也可直接导入；程序通过电话、SSN、英文姓名、年龄、州和邮编的组合结构识别，不会把第一条人员资料当成表头。数字邮编丢失的单个前导零会自动补齐。

## 代理格式

每行一套：

```text
host:port:user:password|https://刷新链接
```

GUI 可用 `＋` 添加，用 `－` 删除。首次启动会从本机 `设计思路.txt` 自动读取符合格式的代理行；GUI 保存项位于 `.mylife_gui_settings.json`，该文件不会进入 Git 仓库或 Release。

Chromium 不支持带账号密码的 SOCKS5 直连，`socks_bridge.py` 会为每套代理自动创建仅监听 `127.0.0.1` 的本地 SOCKS5 桥，再把浏览器固定绑定到对应代理。域名请求会原样交给远端代理解析；远端动态代理出现瞬时连接失败时，本地桥会先内部短重试 3 次，再决定是否向浏览器返回失败。

Patchright 的真 headless 模式会影响 Managed Turnstile token 签发，因此 GUI 的“无头”模式内部仍运行真实 Chrome，再只隐藏本次会话新增的窗口；不会触碰用户已有 Chrome 窗口。“小窗口”则保留可见浏览器便于实时观察。

## HTTP 接口模式

MyLife 的公开搜索入口会先经过 Cloudflare，普通 `requests` 直连会返回 403。因此 HTTP 模式采用浏览器/HTTP 接力，而不是盲目直连：

1. 每个 worker 首次查询时用自己的 Patchright Chrome 完成 Cloudflare 会话初始化；
2. 同步该浏览器的 Cookie、User-Agent 和对应 SOCKS5 代理出口到独立 `requests.Session`；
3. 搜索分页和人物详情优先由 HTTP 会话读取，继续使用既有 HTML 解析、候选匹配和输出流程；
4. 会话失效自动重新初始化一次，仍不兼容时使用当前浏览器文档兜底；
5. 单人结束后保留 Cloudflare 会话供下一条复用，但清空当前页面、权限和浏览器缓存。

HTTP 模式不会改变 SQLite、CSV、输入删行或字段规则，可以直接续跑已有断点。浏览器模式仍是默认值；需要启用时在 GUI“查询方式”中选择 `HTTP接口`。

## 输出

- 实时 CSV：`输出目录/<输入文件名>_MyLife结果.csv`
- SQLite：`输出目录/.mylife_runtime/state.sqlite3`
- 日志：`输出目录/logs/run_YYYYMMDD_HHMMSS.log`

CSV 每写一行都会 `flush + fsync`。输出完整保留原输入列，并在最后追加 `生日`、`性别`、`星座`、`备注原因`；备注会写明最终命中的搜索方式、完整搜索范围、结果状态、命中证据或未搜索到的原因。重复判断比较整行全部字段：首列相同但后续资料不同的记录分别处理，只有整行完全相同的副本才合并；批次正常结束后按完整原始行删除已经明确输出的行。技术失败行保留供下次继续。SQLite 已有结果可自动回放补齐被意外删除的实时 CSV；搜索策略升级时仅重试旧版空生日结果并精确重建 CSV，不重复已有生日行。

## 模块

| 文件 | 职责 |
|---|---|
| `main.py` | 正式入口 |
| `bootstrap.py` | 缺失依赖检查与安装 |
| `gui.py` | GUI、代理增删、实时监控 |
| `controller.py` | 生命周期、线程、断点、批次收尾 |
| `browser_worker.py` | Patchright 浏览器、人类式节奏、搜索/详情流程 |
| `http_worker.py` | Cloudflare 浏览器初始化、HTTP 会话复用、自动重建与浏览器兜底 |
| `identity_matcher.py` | 年龄分层、多信号身份评分、唯一候选选择 |
| `search_planner.py` | 同龄候选判断、曾用名清洗、跨搜索候选合并去重 |
| `demographics_enricher.py` | 严格同身份档案字段补充、完整生日确定星座 |
| `cross_row_enricher.py` | 同姓名+完整电话/邮箱/地址的跨输入行无冲突字段补充 |
| `remark_builder.py` | 生成搜索方式、命中/未命中原因备注 |
| `cloudflare_handler.py` | 持续轮询晚加载 Turnstile 控件并执行真实鼠标点击 |
| `turnstile_harvester.py` | 已有 Cloudflare/Turnstile 模块（正式版未改） |
| `proxy_pool.py` | 代理解析、检查、刷新、地理信息、本地桥 |
| `socks_bridge.py` | 远端认证 SOCKS5 的本地无认证桥 |
| `input_loader.py` | TXT/CSV/XLSX 自动识别 |
| `mylife_parser.py` | 搜索页、分页、详情和生日解析 |
| `database.py` | SQLite WAL 与单写线程 |
| `output_writer.py` | UTF-8 BOM CSV 实时输出 |
| `source_rewriter.py` | 批次结束后一次性重建输入 |
| `runtime_monitor.py` | 数据库/CSV/线程/日志实时健康快照 |
| `models.py` | 数据模型 |

## 验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m unittest discover -s tests -v
python -B main.py --check
```
