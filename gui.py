from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from controller import AppController
from models import RunConfig
from proxy_pool import load_proxy_lines_from_design, parse_proxy_line
from version import __version__


class ProxyRow:
    def __init__(self, owner: "MainWindow", parent: ttk.Frame, value: str = "") -> None:
        self.owner = owner
        self.frame = ttk.Frame(parent)
        self.label = ttk.Label(self.frame, width=7)
        self.label.pack(side="left", padx=(0, 4))
        self.value = tk.StringVar(value=value)
        self.entry = ttk.Entry(self.frame, textvariable=self.value)
        self.entry.pack(side="left", fill="x", expand=True)
        self.remove_button = ttk.Button(self.frame, text="－", width=3, command=lambda: owner.remove_proxy(self))
        self.remove_button.pack(side="left", padx=(4, 0))
        self.frame.pack(fill="x", pady=2)

    def destroy(self) -> None:
        self.frame.destroy()


class MainWindow:
    def __init__(self, root: tk.Tk, project_dir: Path | None = None) -> None:
        self.root = root
        self.project_dir = (project_dir or Path(__file__).resolve().parent).resolve()
        self.settings_path = self.project_dir / ".mylife_gui_settings.json"
        self.controller: AppController | None = None
        self.proxy_rows: list[ProxyRow] = []
        self.root.title(f"MyLife 数据采集正式版 v{__version__}")
        self.root.geometry("1040x760")
        self.root.minsize(900, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self._load_settings()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        files = ttk.LabelFrame(outer, text="输入与输出", padding=8)
        files.pack(fill="x")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        ttk.Label(files, text="导入文件", width=10).grid(row=0, column=0, sticky="w")
        ttk.Entry(files, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(files, text="选择…", command=self._pick_input).grid(row=0, column=2)
        ttk.Label(files, text="输出目录", width=10).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(files, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        ttk.Button(files, text="选择…", command=self._pick_output).grid(row=1, column=2, pady=(6, 0))
        files.columnconfigure(1, weight=1)

        controls = ttk.LabelFrame(outer, text="运行配置", padding=8)
        controls.pack(fill="x", pady=(8, 0))
        self.thread_var = tk.IntVar(value=1)
        self.mode_var = tk.StringVar(value="小窗口")
        self.proxy_enabled_var = tk.BooleanVar(value=False)
        ttk.Label(controls, text="线程数量").pack(side="left")
        ttk.Spinbox(controls, from_=1, to=100, width=6, textvariable=self.thread_var).pack(side="left", padx=(4, 18))
        ttk.Label(controls, text="浏览器模式").pack(side="left")
        ttk.Combobox(controls, values=("小窗口", "无头"), state="readonly", width=9, textvariable=self.mode_var).pack(side="left", padx=(4, 18))
        ttk.Checkbutton(controls, text="启动 SOCKS5 代理池", variable=self.proxy_enabled_var).pack(side="left")
        self.start_button = ttk.Button(controls, text="开始", command=self.start)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="right", padx=(0, 6))

        proxies = ttk.LabelFrame(outer, text="代理池（host:port:user:password|刷新链接）", padding=8)
        proxies.pack(fill="x", pady=(8, 0))
        toolbar = ttk.Frame(proxies)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="每个线程固定一套代理；刷新链接严格执行 3 分钟冷却。", foreground="#555").pack(side="left")
        ttk.Button(toolbar, text="＋ 添加代理", command=self.add_proxy).pack(side="right")
        self.proxy_frame = ttk.Frame(proxies)
        self.proxy_frame.pack(fill="x", pady=(4, 0))

        status_frame = ttk.LabelFrame(outer, text="实时监控", padding=8)
        status_frame.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="待机")
        self.count_var = tk.StringVar(value="总数 0｜完成 0｜待处理 0｜失败 0")
        self.output_status_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.status_var, width=12).pack(side="left")
        ttk.Label(status_frame, textvariable=self.count_var).pack(side="left", padx=(12, 0))
        ttk.Label(status_frame, textvariable=self.output_status_var, foreground="#365f91").pack(side="right")

        logs = ttk.LabelFrame(outer, text="实时日志 / 浏览器 / DevTools / 输入输出状态", padding=6)
        logs.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(logs, wrap="word", height=18, state="disabled", font=("Consolas", 10))
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def add_proxy(self, value: str = "") -> None:
        self.proxy_rows.append(ProxyRow(self, self.proxy_frame, value))
        self._renumber_proxies()

    def remove_proxy(self, row: ProxyRow) -> None:
        if row not in self.proxy_rows:
            return
        self.proxy_rows.remove(row)
        row.destroy()
        self._renumber_proxies()

    def _renumber_proxies(self) -> None:
        for index, row in enumerate(self.proxy_rows, 1):
            row.label.configure(text=f"代理{index}")
            row.remove_button.configure(state="normal" if len(self.proxy_rows) > 1 else "disabled")

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="选择输入文件",
            filetypes=(("支持的输入", "*.txt *.csv *.xlsx *.xlsm"), ("所有文件", "*.*")),
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).resolve().parent / "MyLife输出"))

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

    def _load_settings(self) -> None:
        data: dict[str, object] = {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass
        self.input_var.set(str(data.get("input_file") or ""))
        self.output_var.set(str(data.get("output_dir") or ""))
        self.thread_var.set(int(data.get("thread_count") or 1))
        self.mode_var.set(str(data.get("browser_mode") or "小窗口"))
        self.proxy_enabled_var.set(bool(data.get("proxy_enabled") or False))
        lines = [str(x) for x in data.get("proxy_lines", [])] if isinstance(data.get("proxy_lines"), list) else []
        if not lines:
            lines = load_proxy_lines_from_design(self.project_dir / "设计思路.txt")
        for line in lines or [""]:
            self.add_proxy(line)

    def _save_settings(self) -> None:
        data = {
            "input_file": self.input_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "thread_count": self.thread_var.get(),
            "browser_mode": self.mode_var.get(),
            "proxy_enabled": self.proxy_enabled_var.get(),
            "proxy_lines": [row.value.get().strip() for row in self.proxy_rows if row.value.get().strip()],
        }
        temp = self.settings_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.settings_path)

    def _config(self) -> RunConfig:
        input_file = Path(self.input_var.get().strip()).expanduser().resolve()
        output_dir_text = self.output_var.get().strip()
        output_dir = Path(output_dir_text).expanduser().resolve() if output_dir_text else input_file.parent / "MyLife输出"
        lines = [row.value.get().strip() for row in self.proxy_rows if row.value.get().strip()]
        if self.proxy_enabled_var.get():
            for index, line in enumerate(lines, 1):
                parse_proxy_line(line, index)
            if self.thread_var.get() > len(lines):
                raise ValueError("线程数量不可以超过代理数量")
        return RunConfig(
            input_file=input_file,
            output_dir=output_dir,
            thread_count=int(self.thread_var.get()),
            browser_mode=self.mode_var.get(),
            proxy_enabled=bool(self.proxy_enabled_var.get()),
            proxy_lines=lines,
        )

    def _append_log(self, message: str) -> None:
        def update() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, update)

    def _progress(self, state: dict[str, int | str]) -> None:
        def update() -> None:
            status = str(state.get("status", ""))
            self.status_var.set(status)
            self.count_var.set(
                f"总数 {state.get('total', 0)}｜完成 {state.get('completed', 0)}｜"
                f"待处理 {state.get('pending', 0)}｜失败 {state.get('failed', 0)}｜"
                f"CSV {state.get('output_rows', 0)}｜生日 {state.get('birthdays', 0)}｜"
                f"性别 {state.get('genders', 0)}｜星座 {state.get('zodiacs', 0)}"
            )
            output = str(state.get("output", ""))
            self.output_status_var.set(f"实时 CSV：{output}" if output else "")
            if status in {"已完成", "已停止", "失败"}:
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")

        self.root.after(0, update)

    def start(self) -> None:
        try:
            config = self._config()
            if not config.input_file.is_file():
                raise FileNotFoundError("请选择存在的输入文件")
            self._save_settings()
            self.controller = AppController(config, self._append_log, self._progress)
            self.controller.start_async()
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.status_var.set("启动中")
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc), parent=self.root)

    def stop(self) -> None:
        if self.controller:
            self.controller.stop()
            self.stop_button.configure(state="disabled")
            self.status_var.set("正在停止")

    def _on_close(self) -> None:
        if self.controller and self.controller.thread and self.controller.thread.is_alive():
            self.controller.stop()
            self.root.after(250, self._wait_close)
        else:
            self.root.destroy()

    def _wait_close(self) -> None:
        if self.controller and self.controller.thread and self.controller.thread.is_alive():
            self.root.after(250, self._wait_close)
        else:
            self.root.destroy()


def run_gui(auto_start: bool = False) -> None:
    root = tk.Tk()
    window = MainWindow(root)
    if auto_start:
        root.after(900, window.start)
    root.mainloop()
