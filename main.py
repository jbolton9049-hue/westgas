# -*- coding: utf-8 -*-
"""《天然气管理工具》主程序 - 图形化操作版（鼠标点选）

功能：
  1. 资料收集（单个/批量）
  2. 资料处理（文字/去噪/打标/台账）
  3. 智能记忆训练（自动制卡/间隔复习/掌握度/提醒）
  4. 配置 AI 工具
"""

import os
import re
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import collect
import process as proc
import train
import search
import maintenance
import scheduled_tasks
import backup
import task_manager
from ai_tools import (
    load_config,
    save_config,
    list_available_tools,
    DOMAIN_LABELS,
    ATTR_LABELS,
    classification_label,
    record_rule_feedback,
    CONFIG_PATH,
    get_api_key,
    validate_config,
)

APP_TITLE = "天然气管理知识工具 v1.1"
FONT = ("Microsoft YaHei", 12)
FONT_SMALL = ("Microsoft YaHei", 11)
FONT_MONO = ("Consolas", 11)
COLORS = {
    "bg": "#eef2f5",
    "panel": "#f7f9fc",
    "surface": "#ffffff",
    "navy": "#163b5c",
    "navy_dark": "#102d46",
    "blue_soft": "#dce8f2",
    "text": "#243746",
    "muted": "#526574",
    "green": "#168a4a",
    "green_dark": "#0f6f3b",
    "amber": "#b7791f",
    "red": "#b83232",
}


def configure_theme(root):
    """统一桌面控件的字体、间距和工业管理色板。"""
    root.option_add("*Font", FONT)
    for widget in ("Label", "Button", "Entry", "Text", "Listbox", "Radiobutton", "Checkbutton"):
        root.option_add(f"*{widget}.Font", FONT)
    root.option_add("*Button.Padx", 14)
    root.option_add("*Button.Pady", 7)
    root.option_add("*Entry.Relief", "solid")
    root.option_add("*Entry.BorderWidth", 1)
    root.option_add("*Entry.HighlightThickness", 1)
    root.option_add("*Entry.Background", COLORS["surface"])
    root.option_add("*Entry.Foreground", COLORS["text"])
    root.option_add("*Entry.SelectBackground", COLORS["blue_soft"])
    root.option_add("*Entry.SelectForeground", COLORS["navy_dark"])
    root.option_add("*Text.BorderWidth", 1)
    root.option_add("*Text.Background", COLORS["surface"])
    root.option_add("*Text.Foreground", COLORS["text"])
    root.option_add("*Listbox.BorderWidth", 1)
    root.option_add("*Listbox.Background", COLORS["surface"])
    root.option_add("*Listbox.Foreground", COLORS["text"])
    root.option_add("*Listbox.SelectBackground", COLORS["navy"])
    root.option_add("*Listbox.SelectForeground", COLORS["surface"])
    root.option_add("*Radiobutton.Foreground", COLORS["text"])
    root.option_add("*Checkbutton.Foreground", COLORS["text"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT, padding=(16, 9),
                    background=COLORS["blue_soft"], foreground=COLORS["navy"])
    style.map("TNotebook.Tab", background=[("selected", COLORS["navy"])],
              foreground=[("selected", COLORS["surface"])])
    style.configure("TCombobox", font=FONT, padding=5)


def _code_from_label(value):
    """Read the original code back from a displayed ``CODE description``."""
    return str(value or "").split(" ", 1)[0].strip()


def _hit_summary(hits):
    """Turn keyword hit counts into a short reviewer friendly explanation."""
    if not hits:
        return "未命中明确关键词，需重点人工判断"
    return "、".join(f"{key}（{count}次）" for key, count in sorted(hits.items()))


def choose_files_from_folder(parent, title="选择文件"):
    """Choose specific supported files from a folder with Ctrl/Shift multi-select."""
    folder = filedialog.askdirectory(title="先选择资料文件夹", parent=parent)
    if not folder:
        return []
    paths = []
    for root, _, names in os.walk(folder):
        for name in names:
            if os.path.splitext(name)[1].lower() in proc.SUPPORTED:
                paths.append(os.path.join(root, name))
    paths.sort(key=lambda p: os.path.relpath(p, folder).lower())
    if not paths:
        messagebox.showinfo("没有可处理文件", "该文件夹及其子文件夹没有支持的资料格式。", parent=parent)
        return []

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=COLORS["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(True, True)
    center(dialog, min(900, max(650, parent.winfo_width() + 80)), 560)
    tk.Label(dialog, text="请选择要处理的文件（Ctrl 多选，Shift 连选）",
             font=("Microsoft YaHei", 14, "bold"), bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(14, 5))
    tk.Label(dialog, text=f"文件夹：{folder}    共发现 {len(paths)} 个支持文件",
             font=FONT_SMALL, bg=COLORS["bg"], fg=COLORS["muted"], anchor="w").pack(fill="x", padx=18)
    frame = tk.Frame(dialog, bg=COLORS["bg"])
    frame.pack(fill="both", expand=True, padx=18, pady=10)
    box = tk.Listbox(frame, selectmode=tk.EXTENDED, exportselection=False,
                     font=FONT, activestyle="none")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    box.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    for path in paths:
        box.insert("end", os.path.relpath(path, folder))
    box.selection_set(0, "end")
    result = []
    def select_all():
        box.selection_set(0, "end")
    def clear_all():
        box.selection_clear(0, "end")
    def add_external():
        extra = filedialog.askopenfilenames(
            title="补充选择其他资料（可多选）",
            filetypes=[("可处理资料", "*.pdf *.docx *.txt *.md *.csv *.xlsx"), ("所有文件", "*.*")],
            parent=dialog,
        )
        for path in extra:
            if path not in paths:
                paths.append(path)
                box.insert("end", os.path.basename(path))
    def confirm():
        result.extend(paths[index] for index in box.curselection())
        dialog.destroy()
    def cancel():
        dialog.destroy()
    controls = tk.Frame(dialog, bg=COLORS["bg"])
    controls.pack(pady=(0, 14))
    tk.Button(controls, text="全选", command=select_all, font=FONT,
              bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=12).pack(side="left", padx=4)
    tk.Button(controls, text="清空", command=clear_all, font=FONT,
              bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=12).pack(side="left", padx=4)
    tk.Button(controls, text="补充文件", command=add_external, font=FONT,
              bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=12).pack(side="left", padx=4)
    tk.Button(controls, text="确认选择", command=confirm, font=FONT,
              bg=COLORS["green"], fg="#ffffff", relief="flat", padx=14).pack(side="left", padx=10)
    tk.Button(controls, text="取消", command=cancel, font=FONT,
              bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=14).pack(side="left", padx=4)
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.wait_window()
    return result


def center(win, w, h):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x, y = (sw - w) // 2, (sh - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")


class _BaseWin(tk.Toplevel):
    """带日志输出的子窗口基类。"""
    def __init__(self, title, w, h):
        super().__init__()
        self.title(title)
        # Keep workflow windows associated with the main app so native dialogs
        # and message boxes are stacked above the active workflow.
        self.transient(self.master)
        self.configure(bg=COLORS["bg"])
        center(self, w, h)
        self.minsize(min(w, 720), min(h, 520))
        self.lift()
        self.focus_force()
        self._log = None
        self._ui_queue = queue.Queue()
        self.after(50, self._drain_ui_queue)

    def add_log(self):
        self._log = scrolledtext.ScrolledText(self, height=12, font=FONT_MONO,
                                              bg=COLORS["surface"], fg=COLORS["text"],
                                              insertbackground=COLORS["navy"])
        self._log.pack(fill="both", expand=True, padx=12, pady=12)

    def out(self, text):
        self._ui_queue.put(lambda: self._append_log(text))

    def run_on_ui(self, callback):
        self._ui_queue.put(callback)

    def _drain_ui_queue(self):
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._drain_ui_queue)

    def _append_log(self, text):
        if self._log and self._log.winfo_exists():
            self._log.insert("end", text + "\n")
            self._log.see("end")

    def run_async(self, fn):
        self.configure(cursor="watch")
        threading.Thread(target=self._wrap, args=(fn,), daemon=True).start()

    def _wrap(self, fn):
        try:
            fn()
        except Exception as e:
            self.out(f"❌ 出错：{e}")
        finally:
            self.run_on_ui(lambda: self.configure(cursor=""))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title(APP_TITLE)
        self.configure(bg=COLORS["bg"])
        configure_theme(self)
        center(self, 560, 650)
        self._build_menu()
        self.resizable(False, False)
        self.deiconify()
        self.after(250, self._first_run_setup)

    def _first_run_setup(self):
        if os.path.isfile(CONFIG_PATH):
            return
        messagebox.showinfo(
            "首次运行设置",
            "这是首次运行。请在系统设置中选择知识库目录；不配置 API Key 也可以使用本地规则版。",
            parent=self,
        )
        self.open_config()

    def _build_menu(self):
        header = tk.Frame(self, bg=COLORS["navy"], height=122)
        header.pack(fill="x", padx=18, pady=(18, 16))
        header.pack_propagate(False)
        tk.Label(header, text="天然气管理知识工具", font=("Microsoft YaHei", 22, "bold"),
                 bg=COLORS["navy"], fg=COLORS["surface"]).pack(anchor="w", padx=24, pady=(23, 2))
        tk.Label(header, text="制度资料 · 管理问题 · 训练闭环", font=FONT_SMALL,
                 bg=COLORS["navy"], fg="#d8e6ef").pack(anchor="w", padx=26)
        tk.Label(self, text="请选择要进入的工作模块", font=FONT,
                 bg=COLORS["bg"], fg=COLORS["muted"]).pack(anchor="w", padx=30, pady=(0, 8))

        items = [
            ("📥 资料收集（单个 / 批量）", self.open_collect),
            ("🔎 知识检索", self.open_search),
            ("🎯 智能记忆训练", self.open_train),
            ("📘 规则版原则与改进记录", self.open_rule_guide),
            ("⚙️ 系统设置", self.open_config),
        ]
        for text, cmd in items:
            tk.Button(self, text=text, command=cmd, font=FONT,
                      bg=COLORS["surface"], fg=COLORS["navy"], relief="flat", bd=0,
                      activebackground=COLORS["blue_soft"], activeforeground=COLORS["navy_dark"],
                      width=30, pady=13, cursor="hand2").pack(fill="x", padx=30, pady=5)

        tk.Button(self, text="退出", command=self.destroy, font=FONT,
                  bg=COLORS["red"], fg="#fff", relief="flat", bd=0,
                  activebackground="#962c2c", width=30, pady=10, cursor="hand2").pack(fill="x", padx=30, pady=(20, 22))

    def open_rule_guide(self):
        win = _BaseWin("规则版处理原则与反馈", 900, 680)
        tk.Label(win, text="规则版：本地、可解释、可追溯的建议", font=("Microsoft YaHei", 16, "bold"),
                 bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(14, 5))
        text = scrolledtext.ScrolledText(win, font=FONT, wrap="word", bg=COLORS["surface"], fg=COLORS["text"])
        text.pack(fill="both", expand=True, padx=18, pady=10)
        text.insert("end", "处理原则\n\n"
                    "1. 打标：统计资料中领域和属性关键词命中次数，取最高项；无明确命中时使用 B3 安全管理 / P1 管理问题作为待人工确认的保守建议。\n"
                    "2. 问题提取：按句子切分，只保留包含‘未、缺、不到位、不清、风险、矛盾’等问题标记的片段；每条保留原文依据，最多 5 条。\n"
                    "3. 管理分析：使用固定的责任、流程、检查、考核、复盘模板；事实不足时标记待复核，不把模板当成原文事实。\n"
                    "4. 人工优先：规则版只给建议，人工审核窗口中选择不同意或未选择的条目不会进入台账和后续总结。\n\n"
                    "自我改进方式\n\n"
                    "在人工审核窗口修正领域/属性后，程序会把原文指纹、原建议和人工确认结果追加到知识库/09_周期汇总/规则版反馈.jsonl。相同清洗文本再次处理时，会优先复用这条人工确认结果。每条反馈都保留时间和来源，可审计、可删除，不会静默修改全局关键词规则。\n\n"
                    "DeepSeek 与规则版的区别\n\n"
                    "DeepSeek 是远程模型调用，处理日志会显示每个环节是否成功；接口失败、返回格式无效时会明确记录原因并回退规则版。规则版不调用网络，也不会冒充 DeepSeek。")
        text.configure(state="disabled")
        tk.Button(win, text="关闭", font=FONT, command=win.destroy,
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=7).pack(pady=(0, 12))

    def open_update_review(self):
        """Visual review gate for periodic-maintenance drafts."""
        win = _BaseWin("待审核更新", 1120, 760)
        tk.Label(win, text="周期治理待审核更新", font=("Microsoft YaHei", 16, "bold"),
                 bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(12, 4))
        tk.Label(win, text="选择草稿查看原文和生成结果；批准后仍需点击“发布已审核更新”。",
                 font=FONT_SMALL, bg=COLORS["bg"], fg=COLORS["muted"]).pack(pady=(0, 8))
        body = tk.Frame(win, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=14)
        left = tk.Frame(body, bg=COLORS["bg"], width=340)
        left.pack(side="left", fill="y", padx=(0, 10))
        right = tk.Frame(body, bg=COLORS["bg"])
        right.pack(side="left", fill="both", expand=True)
        box = tk.Listbox(left, selectmode="browse", exportselection=False, font=FONT,
                         bg=COLORS["surface"], fg=COLORS["text"])
        box.pack(fill="both", expand=True)
        detail = scrolledtext.ScrolledText(right, font=FONT_MONO, wrap="word",
                                           bg=COLORS["surface"], fg=COLORS["text"])
        detail.pack(fill="both", expand=True)
        state = {"drafts": []}

        def show_current(*_):
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            selected = box.curselection()
            if not selected:
                detail.insert("end", "当前没有待审核草稿。")
            else:
                draft = state["drafts"][selected[0]]
                detail.insert("end", draft["text"])
                source = draft["meta"].get("source_path")
                if source and os.path.isfile(source):
                    try:
                        source_text = proc.extract_text(source)
                        detail.insert("end", "\n\n================ 原始资料当前文字 ================\n\n")
                        detail.insert("end", source_text[:30000])
                    except Exception as exc:
                        detail.insert("end", f"\n\n原始资料读取失败：{exc}")
            detail.configure(state="disabled")

        def refresh():
            state["drafts"] = maintenance.list_pending_drafts()
            box.delete(0, "end")
            for draft in state["drafts"]:
                box.insert("end", draft["name"])
            show_current()

        def change_status(status):
            selected = box.curselection()
            if not selected:
                messagebox.showwarning("提示", "请先选择一份待审核草稿。", parent=win)
                return
            draft = state["drafts"][selected[0]]
            try:
                maintenance.update_draft_status(draft["path"], status)
                refresh()
                win.out(f"✅ {draft['name']} 已标记为 {status}")
            except Exception as exc:
                messagebox.showerror("审核失败", str(exc), parent=win)

        def publish():
            try:
                records = maintenance.publish_approved("rule")
                win.out(f"✅ 已发布 {len(records)} 条审核通过的更新")
                refresh()
            except Exception as exc:
                messagebox.showerror("发布失败", str(exc), parent=win)

        box.bind("<<ListboxSelect>>", show_current)
        controls = tk.Frame(win, bg=COLORS["bg"])
        controls.pack(pady=10)
        for label, status, color in [("批准", "approved", COLORS["green"]),
                                     ("退回", "rejected", COLORS["amber"])]:
            tk.Button(controls, text=label, font=FONT, command=lambda s=status: change_status(s),
                      bg=color, fg="#fff", relief="flat", padx=16, pady=6).pack(side="left", padx=5)
        tk.Button(controls, text="刷新", font=FONT, command=refresh,
                  bg="#fff", fg=COLORS["navy"], relief="flat", padx=16, pady=6).pack(side="left", padx=5)
        tk.Button(controls, text="发布已审核更新", font=FONT, command=publish,
                  bg=COLORS["navy"], fg="#fff", relief="flat", padx=16, pady=6).pack(side="left", padx=5)
        refresh()
        win.add_log()

    # ---------- 1. 收集 ----------
    def open_collect(self):
        win = _BaseWin("资料收集与人工审核", 780, 700)
        mode = tk.StringVar(value="single")
        selected = []
        prepared = []
        approved = {}
        status_var = tk.StringVar(value="尚未选择资料")
        count_var = tk.StringVar(value="已选文件：0")
        tool_var = tk.StringVar(value="rule")

        tk.Label(win, text="第一步：选择资料", font=("Microsoft YaHei", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(10, 4))
        mode_row = tk.Frame(win, bg=COLORS["bg"])
        mode_row.pack()
        tk.Radiobutton(mode_row, text="单个文件", variable=mode, value="single",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        tk.Radiobutton(mode_row, text="文件夹批量导入", variable=mode, value="batch",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        select_row = tk.Frame(win, bg=COLORS["bg"])
        select_row.pack(fill="x", padx=20, pady=5)
        tk.Label(select_row, textvariable=status_var, font=FONT, bg=COLORS["bg"],
                 fg=COLORS["text"], anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(select_row, textvariable=count_var, font=FONT, bg=COLORS["bg"],
                 fg=COLORS["navy"]).pack(side="right")
        file_box = scrolledtext.ScrolledText(win, height=6, font=FONT_MONO)
        file_box.pack(fill="x", padx=20, pady=(0, 8))
        file_box.configure(state="disabled")

        def refresh_selected():
            file_box.configure(state="normal")
            file_box.delete("1.0", "end")
            file_box.insert("end", "\n".join(selected) if selected else "（请选择文件或文件夹）")
            file_box.configure(state="disabled")
            count_var.set(f"已选文件：{len(selected)}")
            status_var.set("✅ 已选择资料" if selected else "尚未选择资料")

        def choose_files():
            selected.clear()
            prepared.clear()
            approved.clear()
            for child in review_frame.winfo_children():
                child.destroy()
            if mode.get() == "single":
                paths = filedialog.askopenfilenames(
                    title="选择资料文件（可多选）",
                    filetypes=[("可处理资料", "*.pdf *.docx *.txt *.md *.csv *.xlsx"), ("所有文件", "*.*")],
                    parent=win,
                )
                selected.extend(paths)
            else:
                selected.extend(choose_files_from_folder(win, "选择要导入的资料"))
            refresh_selected()

        tk.Button(win, text="选择文件 / 文件夹", font=FONT, command=choose_files,
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=7).pack(pady=(0, 8))

        tk.Label(win, text="第二步：选择 AI 工具或规则版", font=("Microsoft YaHei", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(6, 4))
        tool_row = tk.Frame(win, bg=COLORS["bg"])
        tool_row.pack()
        for key, name in list_available_tools():
            tk.Radiobutton(tool_row, text=name, variable=tool_var, value=key,
                           font=FONT, bg=COLORS["bg"]).pack(side="left", padx=7)

        review_frame = tk.LabelFrame(win, text="第三步：人工审核打标结果（勾选后才能生成闭环）",
                                     font=FONT, bg=COLORS["bg"], padx=8, pady=5)
        review_frame.pack(fill="x", padx=20, pady=8)

        review_results = []
        review_window = {"window": None}
        finalizing = {"value": False}

        def open_review_dialog():
            """Open the review step as a real modal dialog owned by the workflow."""
            if not review_results:
                return
            current = review_window["window"]
            if current is not None and current.winfo_exists():
                current.lift()
                current.focus_force()
                return

            review_win = tk.Toplevel(win)
            review_window["window"] = review_win
            review_win.title("人工审核确认")
            review_win.configure(bg=COLORS["bg"])
            review_win.transient(win)
            review_win.resizable(True, True)
            screen_w = review_win.winfo_screenwidth()
            screen_h = review_win.winfo_screenheight()
            review_w = min(1600, max(760, int(screen_w * 0.92)))
            review_h = min(1050, max(560, int(screen_h * 0.88)))
            review_w = min(review_w, max(640, screen_w - 40))
            review_h = min(review_h, max(480, screen_h - 80))
            review_win.minsize(min(760, review_w), min(560, review_h))
            center(review_win, review_w, review_h)
            review_title_font = ("Microsoft YaHei", 18, "bold")
            review_head_font = ("Microsoft YaHei", 14, "bold")
            review_text_font = ("Microsoft YaHei", 13)
            review_small_font = ("Microsoft YaHei", 12)

            tk.Label(review_win, text="人工审核确认",
                     font=review_title_font,
                     bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(14, 4))
            tk.Label(
                review_win,
                text="阅读原始资料和每条问题的依据，然后选择同意或不同意。未选择视为不同意。",
                font=review_text_font, bg=COLORS["bg"], fg=COLORS["muted"],
            ).pack(pady=(0, 2))
            tk.Label(
                review_win,
                text="只有选择“同意”的问题会进入下一环节的总结提炼；点击提交后自动继续。",
                font=review_small_font, bg="#fff8e1", fg="#7a5b00",
                anchor="w", justify="left", padx=8, pady=5,
            ).pack(fill="x", padx=18, pady=(0, 8))

            body = tk.Frame(review_win, bg=COLORS["bg"])
            body.pack(fill="both", expand=True, padx=12)
            canvas = tk.Canvas(body, bg=COLORS["bg"], highlightthickness=0)
            scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
            rows = tk.Frame(canvas, bg=COLORS["bg"])
            rows_window = canvas.create_window((0, 0), window=rows, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            rows.bind(
                "<Configure>",
                lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
            )
            canvas.bind(
                "<Configure>",
                lambda event: canvas.itemconfigure(rows_window, width=event.width),
            )

            for index, (name, result) in enumerate(review_results, 1):
                if not result.get("ok"):
                    tk.Label(
                        rows, text=f"❌ {name}：{result.get('msg')}",
                        font=FONT_SMALL, bg=COLORS["bg"],
                        fg="#962c2c", anchor="w",
                    ).pack(fill="x", pady=4, padx=6)
                    continue
                key = result["source_path"]
                review = approved[key]
                card = tk.LabelFrame(
                    rows, text=f"资料 {index}：{name}",
                    font=review_head_font,
                    bg="#ffffff", fg=COLORS["navy"], padx=10, pady=8,
                )
                card.pack(fill="x", padx=6, pady=6)
                tk.Label(
                    card, text=(
                        f"原始材料文件：{result.get('original_path') or result.get('source_path', '')}\n"
                        f"导入副本：{result.get('source_path', '')}"
                    ),
                    font=review_small_font, bg="#ffffff", fg=COLORS["muted"],
                    anchor="w", justify="left", wraplength=review_w - 110,
                ).pack(fill="x")
                tk.Label(
                    card, text=f"提取后用于打标的字符数：{result.get('chars', 0)}",
                    font=review_small_font, bg="#ffffff", fg="#666",
                    anchor="w",
                ).pack(fill="x", pady=(2, 3))
                tk.Label(
                    card, text=f"文字来源：{result.get('extraction_method', '自动提取')}",
                    font=review_small_font, bg=COLORS["blue_soft"], fg=COLORS["navy"],
                    anchor="w",
                ).pack(fill="x", pady=(0, 5))
                trace = result.get("processing_trace") or (result.get("analysis") or {}).get("processing_trace") or {}
                trace_summary = (
                    f"调用核验：请求 {trace.get('requested_tool', '未记录')}；"
                    f"实际 {trace.get('actual_tool', '未记录')}；"
                    f"规则版回退：{'是' if trace.get('fallback') else '否'}"
                )
                tk.Label(card, text=trace_summary, font=review_small_font,
                         bg=COLORS["blue_soft"], fg=COLORS["navy"], anchor="w").pack(fill="x", pady=(0, 2))
                task_summary = "；".join(
                    f"{stage}：{detail.get('status', '未知') if isinstance(detail, dict) else detail}"
                    for stage, detail in (trace.get("tasks") or {}).items()
                )
                if task_summary:
                    tk.Label(card, text=task_summary, font=review_small_font,
                             bg="#ffffff", fg=COLORS["muted"], anchor="w",
                             justify="left", wraplength=review_w - 110).pack(fill="x", pady=(0, 5))

                tk.Label(
                    card, text="原始材料（自动提取文本，只读）",
                    font=review_head_font, bg="#ffffff",
                    fg=COLORS["text"], anchor="w",
                ).pack(fill="x")
                material = scrolledtext.ScrolledText(
                    card, height=8, font=review_text_font,
                    wrap="word", relief="solid", bd=1,
                )
                raw_material = result.get("raw_text") or result.get("text") or "（未提取到文本）"
                if len(raw_material) > 20000:
                    raw_material = raw_material[:20000] + "\n\n……原始内容较长，窗口仅显示前 20000 个字符……"
                material.insert(
                    "1.0",
                    raw_material,
                )
                material.configure(state="disabled")
                material.pack(fill="x", pady=(2, 6))

                tag = result.get("tag") or {}
                domain_code = tag.get("domain") or "B3"
                attr_code = tag.get("attr") or "P1"
                tk.Label(card, text="分类建议（可修正，规则版会记录本次人工反馈）",
                         font=review_head_font, bg="#ffffff", fg=COLORS["navy"], anchor="w").pack(fill="x", pady=(0, 2))
                category_row = tk.Frame(card, bg="#ffffff")
                category_row.pack(fill="x", pady=(0, 3))
                tk.Label(category_row, text="领域：", font=review_text_font, bg="#ffffff").pack(side="left")
                ttk.Combobox(category_row, textvariable=review["domain"], state="readonly",
                             values=[classification_label(code, DOMAIN_LABELS) for code in DOMAIN_LABELS],
                             width=24, font=review_small_font).pack(side="left", padx=(0, 16))
                tk.Label(category_row, text="属性：", font=review_text_font, bg="#ffffff").pack(side="left")
                ttk.Combobox(category_row, textvariable=review["attr"], state="readonly",
                             values=[classification_label(code, ATTR_LABELS) for code in ATTR_LABELS],
                             width=25, font=review_small_font).pack(side="left")
                tk.Label(
                    card,
                    text=(
                        f"判定依据：领域关键词 {_hit_summary(tag.get('domain_hits'))}；"
                        f"属性关键词 {_hit_summary(tag.get('attr_hits'))}；"
                        f"工具说明：{tag.get('reason') or tag.get('note') or '规则建议，需人工核对'}"
                    ),
                    font=review_small_font, bg="#ffffff", fg="#666",
                    anchor="w", justify="left", wraplength=review_w - 110,
                ).pack(fill="x", pady=(0, 5))

                analysis = result.get("analysis") or {}
                tk.Label(
                    card, text="AI 管理内涵候选（仅供审核参考）",
                    font=review_head_font, bg="#ffffff",
                    fg=COLORS["navy"], anchor="w",
                ).pack(fill="x", pady=(9, 2))
                tk.Label(
                    card, text=analysis.get("one_sentence") or "（暂无管理内涵）",
                    font=review_text_font, bg="#ffffff", fg=COLORS["text"],
                    anchor="w", justify="left", wraplength=review_w - 110,
                ).pack(fill="x", pady=(0, 5))
                tk.Label(
                    card, text="逐条审核管理问题：同意的条目进入下一环节；不同意或未选择的条目被排除。",
                    font=review_head_font, bg="#ffffff",
                    fg=COLORS["navy"], anchor="w", wraplength=review_w - 110,
                ).pack(fill="x", pady=(6, 3))
                for finding_index, finding_state in enumerate(review["findings"], 1):
                    finding = finding_state["data"]
                    panel = tk.LabelFrame(
                        card, text=f"问题 {finding_index}：{finding.get('title', '待命名问题')}",
                        font=review_head_font, bg="#f8fafc",
                        padx=8, pady=7,
                    )
                    panel.pack(fill="x", pady=4)
                    tk.Label(
                        panel,
                        text=f"原文依据：{finding.get('evidence') or '未给出，需人工核实'}",
                        font=review_text_font, bg="#f8fafc", anchor="w",
                        justify="left", wraplength=review_w - 140,
                    ).pack(fill="x", pady=2)
                    tk.Label(
                        panel,
                        text=f"AI 判断：{finding.get('reason') or '请结合原文核对'}；可信度：{finding.get('confidence', '待确认')}",
                        font=review_small_font, bg="#f8fafc", anchor="w",
                        justify="left", wraplength=review_w - 140,
                    ).pack(fill="x", pady=2)
                    tk.Label(
                        panel, text=f"问题判断：{finding.get('statement') or '未给出'}",
                        font=review_text_font, bg="#f8fafc", anchor="w",
                        justify="left", wraplength=review_w - 140,
                    ).pack(fill="x", pady=2)
                    tk.Label(
                        panel, text=f"管理内涵：{finding.get('management_insight') or '待提炼'}",
                        font=review_text_font, bg="#f8fafc", anchor="w",
                        justify="left", wraplength=review_w - 140,
                    ).pack(fill="x", pady=2)
                    tk.Label(
                        panel,
                        text=(f"领域：{classification_label(finding.get('domain'), DOMAIN_LABELS)}    "
                              f"属性：{classification_label(finding.get('attr'), ATTR_LABELS)}"),
                        font=review_small_font, bg="#f8fafc", fg=COLORS["navy"],
                        anchor="w", justify="left",
                    ).pack(fill="x", pady=2)
                    decision_row = tk.Frame(panel, bg="#f8fafc")
                    decision_row.pack(fill="x", pady=(6, 2))
                    tk.Label(decision_row, text="人工审核：", font=review_text_font,
                             bg="#f8fafc").pack(side="left")
                    for value, label in (("agree", "同意"), ("disagree", "不同意")):
                        tk.Radiobutton(
                            decision_row, text=label, variable=finding_state["decision"],
                            value=value, font=review_text_font, bg="#f8fafc",
                        ).pack(side="left", padx=14)

            def close_review():
                review_win.grab_release()
                review_win.destroy()
                review_window["window"] = None
                win.lift()
                win.focus_force()

            def confirm_review():
                successful = [(name, result) for name, result in review_results if result.get("ok")]
                if not successful:
                    messagebox.showwarning(
                        "无法继续", "没有可审核的资料，请先完成资料处理。",
                        parent=review_win,
                    )
                    return
                # Confirmation is the approval gate. Continue directly to the
                # next workflow step so the reviewer does not have to return
                # to the parent window and click a second submit button.
                chosen = sum(
                    state["decision"].get() == "agree"
                    for _, result in successful
                    for state in approved[result["source_path"]]["findings"]
                )
                status_var.set(f"✅ 同意 {chosen} 条问题，正在进入下一步…")
                win.out(f"✅ 人工审核已提交：同意 {chosen} 条问题，其他问题已排除。")
                finalize_selected(review_win)

            button_row = tk.Frame(review_win, bg=COLORS["bg"])
            button_row.pack(pady=14)
            tk.Button(button_row, text="提交审核并进入下一步", font=review_text_font, command=confirm_review,
                      bg=COLORS["green"], fg="#fff", relief="flat", padx=18, pady=7).pack(side="left", padx=6)
            tk.Button(button_row, text="稍后审核", font=review_text_font, command=close_review,
                      bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=18, pady=7).pack(side="left", padx=6)
            review_win.protocol("WM_DELETE_WINDOW", close_review)
            review_win.grab_set()
            review_win.focus_force()
            review_win.lift()

        def show_review(results):
            for child in review_frame.winfo_children():
                child.destroy()
            review_results[:] = results
            approved.clear()
            for name, result in results:
                if result.get("ok"):
                    key = result["source_path"]
                    domain_code = result["tag"].get("domain") or "B3"
                    attr_code = result["tag"].get("attr") or "P1"
                    findings = result.get("findings") or []
                    finding_states = []
                    for finding in findings:
                        finding_states.append({
                            "data": finding,
                            "decision": tk.StringVar(value=""),
                        })
                    approved[key] = {
                        "findings": finding_states,
                        "domain": tk.StringVar(value=classification_label(domain_code, DOMAIN_LABELS)),
                        "attr": tk.StringVar(value=classification_label(attr_code, ATTR_LABELS)),
                    }
            tk.Label(review_frame, text="处理完成后将弹出独立审核窗口：查看原始材料，逐条核对 AI 问题、原文依据、领域/属性和管理内涵。",
                     font=FONT, bg=COLORS["bg"], fg=COLORS["muted"]).pack(pady=3)
            tk.Button(review_frame, text="重新打开人工审核确认窗口", font=FONT_SMALL,
                      command=open_review_dialog, bg="#ffffff", fg=COLORS["navy"],
                      relief="flat", pady=4).pack(pady=3)
            success_count = sum(1 for _, result in results if result.get("ok"))
            status_var.set(f"✅ 已收集并预处理 {success_count}/{len(results)} 份资料")
            win.out("✅ 转文字/去噪/打标和问题提取完成，请逐条核对原文依据并勾选确认。")
            open_review_dialog()

        def process_selected():
            if not selected:
                messagebox.showwarning("提示", "请先选择资料。", parent=win)
                return
            prepared.clear()
            selected_snapshot = list(selected)
            selected_tool = tool_var.get()
            def run():
                win.run_on_ui(lambda: status_var.set("⏳ 正在收集并处理资料…"))
                results = []
                for path in selected_snapshot:
                    try:
                        imported = collect.collect_single(path, verbose=False)
                        result = proc.prepare_file(imported, selected_tool)
                        result["source_path"] = imported
                        result["original_path"] = path
                        results.append((os.path.basename(path), result))
                    except Exception as exc:
                        results.append((os.path.basename(path), {"ok": False, "msg": str(exc)}))
                prepared.extend(results)
                win.run_on_ui(lambda: show_review(results))
            win.run_async(run)

        def finalize_selected(review_win=None):
            if finalizing["value"]:
                return
            if not prepared:
                messagebox.showwarning("提示", "请先完成自动转文字、去噪和打标。", parent=review_win or win)
                return
            successful = [(name, result) for name, result in prepared if result.get("ok")]
            if not successful or any(result["source_path"] not in approved for _, result in successful):
                messagebox.showwarning(
                    "提示",
                    "请先打开人工审核确认窗口。",
                    parent=review_win or win,
                )
                return
            finalizing["value"] = True
            status_var.set("⏳ 正在进入下一步并生成闭环文件…")
            for _, result in successful:
                review = approved[result["source_path"]]
                reviewed_domain = _code_from_label(review["domain"].get())
                reviewed_attr = _code_from_label(review["attr"].get())
                result["tag"]["domain"] = reviewed_domain
                result["tag"]["attr"] = reviewed_attr
                if tool_var.get() == "rule":
                    try:
                        record_rule_feedback(result.get("text", ""), result["source_path"],
                                             result["tag"], reviewed_domain, reviewed_attr)
                    except Exception as exc:
                        win.out(f"⚠️ 规则反馈未保存：{exc}")
                agreed_findings = []
                for finding_state in review["findings"]:
                    finding = finding_state["data"]
                    # Empty decision is deliberately treated as disagreement.
                    if finding_state["decision"].get() == "agree":
                        agreed_findings.append(finding)
                analysis = result.setdefault("analysis", {})
                analysis["findings"] = agreed_findings
                if agreed_findings:
                    analysis["issue"] = "；".join(
                        item.get("statement", "") for item in agreed_findings
                    )
                    analysis["one_sentence"] = agreed_findings[0].get(
                        "management_insight", analysis.get("one_sentence", "")
                    )
                else:
                    analysis["issue"] = "人工审核未同意任何问题，未进入问题总结提炼。"
                    analysis["one_sentence"] = "本次审核未确认可进入总结提炼的问题。"
                result["tag"]["note"] = (
                    f"人工审核同意 {len(agreed_findings)} 条问题，未选择或不同意的条目已排除"
                )
            if review_win is not None and review_win.winfo_exists():
                review_win.grab_release()
                review_win.destroy()
                review_window["window"] = None
            def run():
                for name, result in successful:
                    final = proc.finalize_processed(result["source_path"], result, tool_var.get())
                    win.out(f"✅ {name} 已完成管理内涵和自动闭环：{final['record_id']}")
                def finish_ui():
                    messagebox.showinfo("完成", f"已完成 {len(successful)} 份资料的管理内涵和自动闭环。", parent=win)
                    win.destroy()
                win.run_on_ui(finish_ui)
            win.run_async(run)

        tk.Button(win, text="开始转文字 / 去噪 / 打标", font=FONT, command=process_selected,
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=7).pack(pady=3)
        tk.Button(win, text="提交人工审核确认并生成管理内涵 / 自动闭环", font=FONT,
                  command=finalize_selected, bg=COLORS["green"], fg="#fff", relief="flat", pady=7).pack(pady=3)
        tk.Button(win, text="返回主页面", font=FONT, command=win.destroy,
                  bg="#ffffff", fg=COLORS["navy"], relief="flat", pady=5).pack(pady=(3, 6))
        win.add_log()

    # ---------- 2. 处理 ----------
    def open_process(self):
        win = _BaseWin("资料处理", 640, 540)
        tk.Label(win, text="选择 AI 工具：", font=FONT, bg=COLORS["bg"]).pack(pady=(8, 2))
        tools = list_available_tools()
        var = tk.StringVar(value=tools[-1][0])
        row = tk.Frame(win, bg=COLORS["bg"])
        row.pack(pady=4)
        for key, name in tools:
            tk.Radiobutton(row, text=name, variable=var, value=key,
                           font=FONT, bg=COLORS["bg"]).pack(side="left", padx=6)

        mode = tk.StringVar(value="single")
        tk.Label(win, text="选择处理方式：", font=FONT, bg=COLORS["bg"]).pack(pady=(6, 2))
        row2 = tk.Frame(win, bg=COLORS["bg"])
        row2.pack()
        tk.Radiobutton(row2, text="单个文件", variable=mode, value="single",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        tk.Radiobutton(row2, text="批量文件夹", variable=mode, value="batch",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)

        # 记录最近一次处理结果，供"下一步"使用
        last_ledger = {"path": None, "text": "", "title": ""}
        task_state = {"manager": task_manager.TaskManager(proc.get_kb_path()), "task": None}
        progress_var = tk.DoubleVar(value=0)
        progress_text = tk.StringVar(value="尚未开始批量任务")
        progress_frame = tk.Frame(win, bg=COLORS["bg"])
        progress_frame.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(progress_frame, textvariable=progress_text, font=FONT_SMALL,
                 bg=COLORS["bg"], fg=COLORS["muted"]).pack(side="left")
        ttk.Progressbar(progress_frame, variable=progress_var, maximum=100,
                        length=260).pack(side="right")

        def _single():
            paths = filedialog.askopenfilenames(
                title="选择资料文件（可多选）",
                    filetypes=[("可处理文件", "*.pdf *.docx *.txt *.md *.csv *.xlsx"), ("所有文件", "*.*")],
                    parent=win)
            if not paths:
                return
            win.out(f"已选择 {len(paths)} 个文件")
            def do():
                for name, r in proc.process_files(paths, var.get()):
                    if r.get("ok"):
                        win.out(f"✅ {name}：处理完成，提取 {r['chars']} 字符")
                        win.out(f"   打标：领域={r['tag'].get('domain')} 属性={r['tag'].get('attr')}（工具:{r['tag'].get('tool')}）")
                        win.out(f"   原始资料：{r['raw_file']}")
                        win.out(f"   台账：{r['ledger_file']}")
                        trace = r.get("processing_trace") or (r.get("analysis") or {}).get("processing_trace") or {}
                        win.out(f"   调用追踪：请求={trace.get('requested_tool', var.get())}，实际={trace.get('actual_tool', '未记录')}，回退={'是' if trace.get('fallback') else '否'}")
                        last_ledger["path"] = r["ledger_file"]
                        last_ledger["title"] = name
                        last_ledger["text"] = r.get("text", "")
                    else:
                        win.out(f"⚠️ {name}: {r.get('msg')}")
            win.run_async(do)

        def _batch():
            paths = choose_files_from_folder(win, "选择要处理的资料")
            if not paths:
                return
            task_state["task"] = task_state["manager"].create(
                "资料批量处理", [os.path.basename(path) for path in paths], {"tool": var.get()}
            )
            progress_var.set(0)
            progress_text.set(f"任务 {task_state['task']['task_id']}：准备处理 {len(paths)} 个文件")
            def on_progress(done, total, item, result):
                win.run_on_ui(lambda: (progress_var.set(done * 100 / max(total, 1)),
                                        progress_text.set(f"已完成 {done}/{total}：{os.path.basename(item)}")))
            def do():
                _, results, state = proc.process_files_managed(
                    paths, var.get(), task_state["manager"], on_progress, task_state["task"]
                )
                ok = sum(1 for _, r in results if r.get("ok"))
                win.out(f"✅ 批量处理完成：成功 {ok}/{len(results)}")
                win.run_on_ui(lambda: progress_text.set(
                    f"任务 {state.get('task_id')}：{state.get('status')}，成功 {ok}/{len(results)}"
                ))
                for fname, r in results:
                    if r.get("ok"):
                        win.out(f"   ✔ {fname} -> 领域{r['tag'].get('domain')}/属性{r['tag'].get('attr')}")
                        trace = r.get("processing_trace") or (r.get("analysis") or {}).get("processing_trace") or {}
                        win.out(f"      调用：{trace.get('actual_tool', '未记录')}；回退={'是' if trace.get('fallback') else '否'}")
                    else:
                        win.out(f"   ✘ {fname}: {r.get('msg')}")
                # 批量：记录最后一个成功结果供下一步
                for fname, r in results:
                    if r.get("ok"):
                        last_ledger["path"] = r["ledger_file"]
                        last_ledger["title"] = fname
                        last_ledger["text"] = r.get("text", "")
            win.run_async(do)

        def pause_task():
            task = task_state.get("task")
            if task:
                task_state["manager"].pause(task["task_id"])
                progress_text.set("任务已暂停，可点击继续")

        def resume_task():
            task = task_state.get("task")
            if task:
                task_state["manager"].resume(task["task_id"])
                progress_text.set("任务已继续")

        def cancel_task():
            task = task_state.get("task")
            if task:
                task_state["manager"].cancel(task["task_id"])
                progress_text.set("任务已取消，未完成文件可稍后重试")

        # 下一步：提炼管理内涵
        def _next_insight():
            if not last_ledger["path"]:
                messagebox.showwarning("提示", "请先处理一份资料，再提炼管理内涵。", parent=win)
                return
            win.out(f"\n→ 正在用 {var.get()} 提炼管理内涵（{last_ledger['title']}）...")
            def do():
                res = proc.extract_insight(last_ledger.get("text") or last_ledger["title"],
                                           tool_key=var.get(), ledger_file=last_ledger["path"])
                content = res.get("content", "")
                if content:
                    win.out("✅ 管理内涵已生成并写入台账：")
                    win.out(content[:600])
                else:
                    win.out(f"⚠️ 提炼未返回内容（{res.get('tool')}）。若选了AI工具但未配置Key，请先到'配置AI工具'填写。")
            win.run_async(do)

        tk.Button(win, text="选择文件 / 文件夹并处理", font=FONT,
                  command=lambda: _single() if mode.get() == "single" else _batch(),
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=8).pack(pady=10)
        control_row = tk.Frame(win, bg=COLORS["bg"])
        control_row.pack(pady=(0, 5))
        tk.Button(control_row, text="暂停", font=FONT_SMALL, command=pause_task).pack(side="left", padx=4)
        tk.Button(control_row, text="继续", font=FONT_SMALL, command=resume_task).pack(side="left", padx=4)
        tk.Button(control_row, text="取消任务", font=FONT_SMALL, command=cancel_task).pack(side="left", padx=4)
        tk.Button(win, text="下一步：提炼管理内涵", font=FONT, command=_next_insight,
                  bg=COLORS["green"], fg="#fff", relief="flat", pady=8).pack(pady=(2, 8))
        tk.Label(win, text="提示：可用不同工具分别处理同一份资料，对比结果后由你判断。",
                 font=("Microsoft YaHei", 10), bg=COLORS["bg"], fg="#888").pack()
        win.add_log()

    # ---------- 3. 自动闭环 ----------
    def open_auto(self):
        win = _BaseWin("自动闭环", 700, 560)
        tk.Label(win, text="一次完成：提取 → 分类 → 管理内涵 → 后果 → 方案 → 事件分析 → 记忆卡片",
                 font=FONT, bg=COLORS["bg"]).pack(pady=(10, 4))
        tools = list_available_tools()
        var = tk.StringVar(value="rule")
        row = tk.Frame(win, bg=COLORS["bg"])
        row.pack(pady=4)
        for key, name in tools:
            tk.Radiobutton(row, text=name, variable=var, value=key,
                           font=FONT, bg=COLORS["bg"]).pack(side="left", padx=6)
        mode = tk.StringVar(value="single")
        row2 = tk.Frame(win, bg=COLORS["bg"])
        row2.pack(pady=3)
        tk.Radiobutton(row2, text="单个文件", variable=mode, value="single",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        tk.Radiobutton(row2, text="批量文件夹", variable=mode, value="batch",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)

        def render_result(name, result):
            if not result.get("ok"):
                win.out(f"✘ {name}: {result.get('msg')}")
                return
            win.out(f"✔ {name}：{result['record_id']}，{result['chars']} 字符")
            for key in ["ledger_file", "solution_file", "event_file", "card_file"]:
                win.out(f"  {key}: {result[key]}")
            tag = result.get("tag", {})
            win.out(f"  标签：{tag.get('domain')} / {tag.get('attr')}")

        def choose_and_run():
            if mode.get() == "single":
                path = filedialog.askopenfilename(
                    title="选择待自动分析资料",
                    filetypes=[("可处理文件", "*.pdf *.docx *.txt *.md *.csv *.xlsx"), ("所有文件", "*.*")],
                    parent=win,
                )
                if not path:
                    return
                win.out(f"已选择：{path}")
                win.run_async(lambda: render_result(os.path.basename(path), proc.automate_file(path, var.get())))
            else:
                paths = choose_files_from_folder(win, "选择要自动分析的资料")
                if not paths:
                    return
                def run_batch():
                    results = proc.automate_files(paths, var.get())
                    ok = sum(1 for _, item in results if item.get("ok"))
                    win.out(f"批量闭环完成：成功 {ok}/{len(results)}")
                    for name, item in results:
                        render_result(name, item)
                win.run_async(run_batch)

        tk.Button(win, text="选择文件 / 文件夹并开始", font=FONT, command=choose_and_run,
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=8).pack(pady=10)
        win.add_log()

    # ---------- 4. 检索 ----------
    def open_search(self):
        win = _BaseWin("知识检索与同类问题汇总", 1000, 720)
        mode = tk.StringVar(value="search")
        mode_row = tk.Frame(win, bg=COLORS["bg"])
        mode_row.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(mode_row, text="功能：", font=FONT, bg=COLORS["bg"]).pack(side="left")
        tk.Radiobutton(mode_row, text="关键词检索", variable=mode, value="search",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        tk.Radiobutton(mode_row, text="同类问题汇总 + 建议措施", variable=mode, value="summary",
                       font=FONT, bg=COLORS["bg"]).pack(side="left", padx=8)
        hint = tk.Label(
            win,
            text="汇总模式默认读取 02_分类台账 中已人工审核的问题；可输入关键词限定汇总范围。",
            font=FONT_SMALL, fg=COLORS["muted"], bg=COLORS["bg"], anchor="w",
        )
        hint.pack(fill="x", padx=12, pady=(0, 2))
        def update_hint(*_):
            if mode.get() == "summary":
                hint.configure(text="汇总模式默认读取 02_分类台账 中已人工审核的问题；可输入关键词限定汇总范围。")
            else:
                hint.configure(text="检索模式搜索分类台账、管理要点、记忆卡片、管理依据和事件分析。")
        mode.trace_add("write", update_hint)
        update_hint()
        row = tk.Frame(win, bg=COLORS["bg"])
        row.pack(fill="x", padx=12, pady=8)
        query = tk.Entry(row, font=FONT)
        query.pack(side="left", fill="x", expand=True, padx=(0, 8))
        filter_row = tk.Frame(win, bg=COLORS["bg"])
        filter_row.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(filter_row, text="领域：", font=FONT_SMALL, bg=COLORS["bg"]).pack(side="left")
        domain_var = tk.StringVar(value="全部")
        domain_box = ttk.Combobox(filter_row, textvariable=domain_var, state="readonly",
                                  values=["全部"] + [classification_label(k, DOMAIN_LABELS) for k in DOMAIN_LABELS],
                                  width=25, font=FONT_SMALL)
        domain_box.pack(side="left", padx=(2, 14))
        tk.Label(filter_row, text="属性：", font=FONT_SMALL, bg=COLORS["bg"]).pack(side="left")
        attr_var = tk.StringVar(value="全部")
        attr_box = ttk.Combobox(filter_row, textvariable=attr_var, state="readonly",
                                values=["全部"] + [classification_label(k, ATTR_LABELS) for k in ATTR_LABELS],
                                width=27, font=FONT_SMALL)
        attr_box.pack(side="left", padx=2)
        result_box = scrolledtext.ScrolledText(win, font=("Microsoft YaHei", 11), wrap="word")
        result_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def do_search():
            result_box.delete("1.0", "end")
            if mode.get() == "summary":
                groups = search.summarize_similar_issues(query.get())
                result_box.insert("end", search.format_issue_summary(groups))
                return
            domain_code = _code_from_label(domain_var.get()) if domain_var.get() != "全部" else None
            attr_code = _code_from_label(attr_var.get()) if attr_var.get() != "全部" else None
            rows = search.search_knowledge(query.get(), domain=domain_code, attr=attr_code)
            if not rows:
                result_box.insert("end", "没有找到匹配内容。\n")
                return
            for index, row_data in enumerate(rows, 1):
                result_box.insert("end", f"[{index}] 命中 {row_data['score']}：{row_data['name']}\n")
                result_box.insert("end", f"路径：{row_data['path']}\n摘要：{row_data['snippet']}\n原文依据：{row_data.get('evidence', row_data['snippet'])}\n\n")

        def save_summary():
            if mode.get() != "summary":
                messagebox.showinfo("保存汇总", "请先选择“同类问题汇总 + 建议措施”模式。", parent=win)
                return
            groups = search.summarize_similar_issues(query.get())
            if not groups:
                messagebox.showwarning("提示", "当前没有可保存的同类问题汇总。", parent=win)
                return
            path = search.save_issue_summary(groups)
            messagebox.showinfo("已保存", f"同类问题汇总已保存：\n{path}", parent=win)

        tk.Button(row, text="执行", font=FONT, command=do_search,
                  bg=COLORS["navy"], fg="#fff", relief="flat", padx=12).pack(side="right")
        save_button = tk.Button(
            row,
            text="保存同类问题汇总",
            font=("Microsoft YaHei", 11, "bold"),
            command=save_summary,
            bg="#168a4a",
            fg="#ffffff",
            activebackground="#0f6f3b",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=5,
        )
        save_button.pack(side="right", padx=(0, 8))
        def show_trends():
            result_box.delete("1.0", "end")
            trends = search.issue_trends()
            result_box.insert("end", "问题趋势统计\n====================\n")
            for item in trends:
                result_box.insert("end", f"{item['month']}｜{item['domain']} / {item['attr']}：{item['count']} 条\n")

        def save_topic_report():
            topic = query.get().strip()
            if not topic:
                messagebox.showwarning("提示", "请先输入专题关键词。", parent=win)
                return
            path = search.save_topic_report(topic)
            result_box.delete("1.0", "end")
            result_box.insert("end", f"专题报告已生成：\n{path}\n\n")
            result_box.insert("end", search.generate_topic_report(topic))

        tk.Button(row, text="趋势统计", font=FONT_SMALL, command=show_trends,
                  bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=8).pack(side="right", padx=(0, 8))
        tk.Button(row, text="生成专题报告", font=FONT_SMALL, command=save_topic_report,
                  bg="#ffffff", fg=COLORS["navy"], relief="flat", padx=8).pack(side="right", padx=(0, 8))
        query.bind("<Return>", lambda _: do_search())

    # ---------- 5. 训练 ----------
    def open_train(self):
        win = _BaseWin("智能记忆训练", 980, 720)
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        primary = {"bg": COLORS["navy"], "fg": "#ffffff", "activebackground": "#162b4d",
                   "activeforeground": "#ffffff", "relief": "flat"}
        green = {"bg": "#168a4a", "fg": "#ffffff", "activebackground": "#0f6f3b",
                 "activeforeground": "#ffffff", "relief": "flat"}

        # 今日训练：看板 + 按间隔重复队列进行自评
        today_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(today_tab, text="今日训练")
        tk.Label(today_tab, text="今日训练看板", font=("Microsoft YaHei", 15, "bold"),
                 bg=COLORS["panel"], fg=COLORS["navy"]).pack(anchor="w", padx=18, pady=(14, 3))
        dash_var = tk.StringVar()
        dash_label = tk.Label(today_tab, textvariable=dash_var, font=FONT, justify="left",
                              anchor="w", bg=COLORS["panel"], fg=COLORS["text"])
        dash_label.pack(fill="x", padx=18, pady=(0, 8))
        weak_var = tk.StringVar()
        tk.Label(today_tab, textvariable=weak_var, font=FONT_SMALL, justify="left",
                 anchor="w", bg=COLORS["panel"], fg="#9b2c2c").pack(fill="x", padx=18, pady=(0, 6))
        due_box = scrolledtext.ScrolledText(today_tab, height=12, font=FONT_SMALL,
                                            wrap="word", bg="#ffffff")
        due_box.pack(fill="both", expand=True, padx=18, pady=6)
        due_box.configure(state="disabled")

        def refresh_dashboard():
            dashboard = train.training_dashboard()
            dash_var.set(
                f"卡片总数：{dashboard['total']}    已掌握（≥{dashboard['mastery_threshold']}%）：{dashboard['mastered']}    "
                f"整体掌握率：{dashboard['mastery_rate']}%\n"
                f"今日待复习：{dashboard['today_due']}    每日上限：{dashboard['daily_limit']}    "
                f"连续训练：{dashboard['streak_days']} 天"
            )
            weak_var.set(f"薄弱知识点：{dashboard['weak']} 张（掌握度低于 {train.WEAK_THRESHOLD}%）")
            due_box.configure(state="normal")
            due_box.delete("1.0", "end")
            due = train.due_cards(limit=dashboard["daily_limit"])
            if not due:
                due_box.insert("end", "今天没有到期卡片。可以从“自动生成”导入知识，或等待下一次复习。")
            else:
                due_box.insert("end", "今日复习队列（先回忆，再点击开始训练）：\n\n")
                for item in due:
                    due_box.insert("end", f"• {item['file']}  掌握度 {item.get('mastery', 0)}%  "
                                             f"第{item['round']}次\n")
            due_box.configure(state="disabled")

        def _card_text(card_name):
            path = os.path.join(train.get_kb_path(), "04_记忆卡片", card_name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                return "", ""
            question = re.search(r"\*\*问题情景\*\*：(.+)", text)
            answer = []
            for label in ("根因", "解决动作", "一句话表达"):
                match = re.search(rf"\*\*{label}\*\*：(.+)", text)
                if match:
                    answer.append(f"{label}：{match.group(1).strip()}")
            return (question.group(1).strip() if question else card_name), "\n".join(answer)

        def start_training():
            queue = train.due_cards(limit=train.training_dashboard()["daily_limit"])
            if not queue:
                messagebox.showinfo("今日训练", "当前没有到期卡片。", parent=win)
                return
            session = _BaseWin("今日间隔复习", 720, 540)
            index = {"value": 0}
            tk.Label(session, text="先独立回忆，再显示答案并自评", font=FONT,
                     bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(12, 4))
            title_var = tk.StringVar()
            tk.Label(session, textvariable=title_var, font=("Microsoft YaHei", 12, "bold"),
                     bg=COLORS["bg"], fg=COLORS["text"]).pack(pady=4)
            question_box = scrolledtext.ScrolledText(session, height=8, font=FONT, wrap="word")
            question_box.pack(fill="x", padx=18, pady=8)
            answer_box = scrolledtext.ScrolledText(session, height=7, font=FONT, wrap="word", bg="#fffdf2")
            answer_box.pack(fill="x", padx=18, pady=4)
            answer_box.configure(state="disabled")
            status_var = tk.StringVar()
            tk.Label(session, textvariable=status_var, font=FONT_SMALL,
                     bg=COLORS["bg"], fg=COLORS["muted"]).pack(pady=4)
            rating_row = tk.Frame(session, bg=COLORS["bg"])
            rating_row.pack(pady=8)
            revealed = {"value": False}

            def show_card():
                item = queue[index["value"]]
                revealed["value"] = False
                question, answer = _card_text(item["file"])
                title_var.set(f"第 {index['value'] + 1}/{len(queue)} 张：{item['file']}")
                question_box.delete("1.0", "end")
                question_box.insert("end", question)
                answer_box.configure(state="normal")
                answer_box.delete("1.0", "end")
                answer_box.insert("end", "点击“显示答案”后查看。")
                answer_box.configure(state="disabled")
                status_var.set("请先回忆，再显示答案")

            def reveal():
                item = queue[index["value"]]
                revealed["value"] = True
                _, answer = _card_text(item["file"])
                answer_box.configure(state="normal")
                answer_box.delete("1.0", "end")
                answer_box.insert("end", answer or "卡片没有可显示的答案，请编辑卡片补充。")
                answer_box.configure(state="disabled")
                status_var.set("请选择：记得、模糊，或忘记")

            def rate(value):
                if not revealed["value"]:
                    messagebox.showinfo("先看答案", "请先点击“显示答案”，再根据实际回忆情况自评。", parent=session)
                    return
                train.record_review(queue[index["value"]]["file"], value)
                index["value"] += 1
                if index["value"] >= len(queue):
                    session.destroy()
                    refresh_dashboard()
                    weak = train.weak_cards()
                    if weak:
                        names = "\n".join(f"• {item['file']}（{item['mastery']}%）" for item in weak[:8])
                        messagebox.showinfo("训练完成 · 薄弱提醒",
                                            f"今日训练已完成，复习间隔和掌握度已更新。\n\n"
                                            f"以下知识点掌握度低于 {train.WEAK_THRESHOLD}%：\n{names}\n\n"
                                            "请在下一次复习时优先回顾。", parent=win)
                    else:
                        messagebox.showinfo("训练完成", "今日训练已完成，复习间隔和掌握度已更新。", parent=win)
                else:
                    show_card()

            tk.Button(session, text="显示答案", font=FONT, command=reveal, **primary).pack(pady=3)
            for text, value, colors in (("记得", "remember", green), ("模糊", "fuzzy", {"bg": "#b7791f", "fg": "#fff", "activebackground": "#975a16", "relief": "flat"}), ("忘记", "forget", {"bg": "#b83232", "fg": "#fff", "activebackground": "#9b2c2c", "relief": "flat"})):
                tk.Button(rating_row, text=text, width=10, font=FONT, command=lambda v=value: rate(v), **colors).pack(side="left", padx=5)
            show_card()

        action_row = tk.Frame(today_tab, bg=COLORS["panel"])
        action_row.pack(fill="x", padx=18, pady=(4, 14))
        tk.Button(action_row, text="开始今日训练", font=("Microsoft YaHei", 11, "bold"), command=start_training, **green).pack(side="left", padx=(0, 8), ipadx=8, ipady=4)
        tk.Button(action_row, text="刷新看板", font=FONT, command=refresh_dashboard, **primary).pack(side="left", ipadx=8, ipady=4)
        refresh_dashboard()

        # 自动生成页：从已沉淀的台账、管理要点和依据库生成待审核卡片
        auto_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(auto_tab, text="自动生成卡片")
        tk.Label(auto_tab, text="从知识库自动生成记忆卡片", font=("Microsoft YaHei", 15, "bold"),
                 bg=COLORS["panel"], fg=COLORS["navy"]).pack(anchor="w", padx=18, pady=(14, 4))
        tk.Label(auto_tab, text="按主题筛选并生成待审核草稿。只有人工通过的卡片才进入今日训练。",
                 font=FONT_SMALL, bg=COLORS["panel"], fg=COLORS["muted"]).pack(anchor="w", padx=18, pady=(0, 8))
        generate_row = tk.Frame(auto_tab, bg=COLORS["panel"])
        generate_row.pack(fill="x", padx=18, pady=5)
        tk.Label(generate_row, text="主题（可选）：", font=FONT, bg=COLORS["panel"]).pack(side="left")
        topic_ent = tk.Entry(generate_row, font=FONT, width=35)
        topic_ent.pack(side="left", padx=6)
        generated_var = tk.StringVar(value="请先生成卡片，或选择已有待审核草稿。")
        tk.Label(auto_tab, textvariable=generated_var, font=FONT_SMALL,
                 bg=COLORS["panel"], fg=COLORS["navy"], anchor="w").pack(fill="x", padx=18, pady=6)
        pending_box = tk.Listbox(auto_tab, font=("Microsoft YaHei", 11), height=17)
        pending_box.pack(fill="both", expand=True, padx=18, pady=8)
        pending_items = []

        def refresh_pending():
            pending_items[:] = train.list_pending_cards()
            pending_box.delete(0, "end")
            for draft in pending_items:
                pending_box.insert("end", draft["name"])
            if not pending_items:
                generated_var.set("当前没有待审核卡片。")

        def review_pending():
            selected = pending_box.curselection()
            if not selected:
                messagebox.showinfo("审核卡片", "请先选中一张待审核卡片。", parent=win)
                return
            draft = pending_items[selected[0]]
            with open(draft["file"], "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            review = tk.Toplevel(win)
            review.title("人工审核记忆卡片")
            review.configure(bg=COLORS["panel"])
            center(review, 820, 660)
            review.transient(win)
            review.grab_set()
            header = re.search(r"^# 记忆卡片：(.+)$", content, re.M)
            source = re.search(r"\*\*来源文件\*\*：(.+)$", content, re.M)
            tk.Label(review, text="审核卡片内容", font=("Microsoft YaHei", 15, "bold"),
                     bg=COLORS["panel"], fg=COLORS["navy"]).pack(anchor="w", padx=18, pady=(12, 3))
            tk.Label(review, text=f"来源：{source.group(1) if source else '未知'}",
                     font=FONT_SMALL, bg=COLORS["panel"], fg=COLORS["muted"],
                     wraplength=770, justify="left").pack(anchor="w", padx=18, pady=(0, 10))
            edit_widgets = {}
            field_labels = (("title", "标题"), ("problem", "问题情景"),
                            ("root_cause", "根因"), ("action", "解决动作"),
                            ("expression", "一句话表达"))
            for key, label in field_labels:
                tk.Label(review, text=label, font=("Microsoft YaHei", 10, "bold"),
                         bg=COLORS["panel"], fg=COLORS["text"]).pack(anchor="w", padx=18, pady=(4, 1))
                editor = tk.Text(review, height=1 if key == "title" else 3,
                                 font=FONT_SMALL, wrap="word")
                editor.pack(fill="x", padx=18)
                if key == "title":
                    value = header.group(1).strip() if header else ""
                else:
                    match = re.search(rf"\*\*{label}\*\*：(.+)$", content, re.M)
                    value = match.group(1).strip() if match else ""
                editor.insert("1.0", value)
                edit_widgets[key] = editor

            button_row = tk.Frame(review, bg=COLORS["panel"])
            button_row.pack(fill="x", padx=18, pady=14)

            def approve():
                edits = {key: editor.get("1.0", "end").strip()
                         for key, editor in edit_widgets.items()}
                try:
                    approved_path = train.approve_card(draft["file"], edits)
                except (ValueError, OSError) as exc:
                    messagebox.showerror("审核失败", str(exc), parent=review)
                    return
                review.destroy()
                refresh_pending()
                refresh_cards()
                refresh_dashboard()
                generated_var.set(f"已通过：{os.path.basename(approved_path)}，现可进入今日训练。")

            def reject():
                train.reject_card(draft["file"])
                review.destroy()
                refresh_pending()
                generated_var.set("卡片已移入“已拒绝”归档目录。")

            tk.Button(button_row, text="通过并加入训练", font=("Microsoft YaHei", 11, "bold"),
                      command=approve, **green).pack(side="left", padx=(0, 8), ipadx=8, ipady=4)
            tk.Button(button_row, text="不同意（归档）", font=FONT, command=reject,
                      bg="#b83232", fg="#fff", activebackground="#9b2c2c", relief="flat").pack(side="left", ipadx=8, ipady=4)

        def generate_from_kb():
            generated = train.generate_cards_from_knowledge(topic_ent.get().strip(), limit=50)
            refresh_pending()
            generated_var.set(f"本次新增 {len(generated)} 张待审核卡片。请逐张核对问题、答案和来源。")
            refresh_dashboard()

        tk.Button(generate_row, text="自动生成卡片", font=("Microsoft YaHei", 11, "bold"), command=generate_from_kb, **green).pack(side="left", padx=6, ipadx=8, ipady=3)
        tk.Button(auto_tab, text="审核选中卡片", font=("Microsoft YaHei", 11, "bold"),
                  command=review_pending, **primary).pack(anchor="w", padx=18, pady=(0, 14), ipadx=8, ipady=4)
        refresh_pending()

        # 卡片管理页：保留手工创建入口，并显示当前卡片掌握度
        manage_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(manage_tab, text="卡片管理")
        left = tk.Frame(manage_tab, bg=COLORS["panel"])
        left.pack(side="left", fill="y", padx=(14, 8), pady=14)
        right = tk.Frame(manage_tab, bg=COLORS["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 14), pady=14)
        card_list = tk.Listbox(left, width=38, height=24, font=FONT_SMALL)
        card_list.pack(fill="y", expand=True)
        detail_box = scrolledtext.ScrolledText(right, font=FONT_SMALL, wrap="word")
        detail_box.pack(fill="both", expand=True)
        detail_box.configure(state="disabled")

        def refresh_cards():
            card_list.delete(0, "end")
            for card in train.list_cards():
                progress = train.card_progress(card)
                card_list.insert("end", f"{progress.get('mastery', 0):>3}%  {card}")

        def show_detail(_event=None):
            selected = card_list.curselection()
            if not selected:
                return
            display = card_list.get(selected[0])
            card = display.split("  ", 1)[-1]
            path = os.path.join(train.get_kb_path(), "04_记忆卡片", card)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                text = "无法读取卡片。"
            progress = train.card_progress(card)
            detail_box.configure(state="normal")
            detail_box.delete("1.0", "end")
            detail_box.insert("end", f"当前掌握度：{progress.get('mastery', 0)}%\n"
                                      f"记忆等级：{progress.get('level', 0)}/5\n"
                                      f"下次复习：{progress.get('due', '未安排')}\n\n{text}")
            detail_box.configure(state="disabled")

        card_list.bind("<<ListboxSelect>>", show_detail)
        tk.Button(left, text="刷新卡片", font=FONT, command=refresh_cards, **primary).pack(fill="x", pady=(8, 0))
        refresh_cards()

        # 手工创建页，保留原有入口
        manual_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(manual_tab, text="手工补充卡片")
        fields = {}
        labels = ["标题", "问题情景", "根因", "解决动作", "一句话表达"]
        for i, lab in enumerate(labels):
            tk.Label(manual_tab, text=lab, font=FONT, bg=COLORS["panel"]).grid(row=i, column=0, sticky="w", padx=18, pady=6)
            ent = tk.Entry(manual_tab, font=FONT, width=65)
            ent.grid(row=i, column=1, padx=8, pady=6, sticky="w")
            fields[lab] = ent

        def _gen_card():
            vals = {k: v.get().strip() for k, v in fields.items()}
            if not vals["标题"] or not vals["问题情景"]:
                messagebox.showwarning("提示", "标题和问题情景必填。", parent=win)
                return
            path = train.generate_card(vals["标题"], vals["问题情景"], vals["根因"], vals["解决动作"], vals["一句话表达"])
            refresh_cards()
            refresh_dashboard()
            messagebox.showinfo("成功", f"卡片已生成：\n{path}", parent=win)

        tk.Button(manual_tab, text="生成记忆卡片", font=("Microsoft YaHei", 11, "bold"), command=_gen_card, **primary).grid(row=6, column=1, sticky="w", padx=8, pady=14)

        # 提醒页
        reminder_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(reminder_tab, text="智能提醒")
        reminder_box = scrolledtext.ScrolledText(reminder_tab, height=25, font=("Microsoft YaHei", 11), wrap="word")
        reminder_box.pack(fill="both", expand=True, padx=18, pady=14)
        reminder_box.insert("end", train.review_reminder())
        reminder_box.configure(state="disabled")
        def refresh_reminder():
            reminder_box.configure(state="normal")
            reminder_box.delete("1.0", "end")
            reminder_box.insert("end", train.review_reminder())
            reminder_box.configure(state="disabled")
        tk.Button(reminder_tab, text="刷新提醒", font=FONT, command=refresh_reminder, **primary).pack(pady=(0, 12))

        # 训练设置页：每日数量和掌握度阈值可调整
        settings_tab = tk.Frame(nb, bg=COLORS["panel"])
        nb.add(settings_tab, text="训练设置")
        settings = train._load_state().get("settings", {})
        tk.Label(settings_tab, text="训练参数", font=("Microsoft YaHei", 15, "bold"),
                 bg=COLORS["panel"], fg=COLORS["navy"]).pack(anchor="w", padx=18, pady=(14, 12))
        settings_row = tk.Frame(settings_tab, bg=COLORS["panel"])
        settings_row.pack(anchor="w", padx=18, pady=6)
        tk.Label(settings_row, text="每日训练上限：", font=FONT, bg=COLORS["panel"]).grid(row=0, column=0, sticky="w", pady=6)
        daily_ent = tk.Entry(settings_row, font=FONT, width=10)
        daily_ent.insert(0, str(settings.get("daily_limit", train.DAILY_TRAINING_LIMIT)))
        daily_ent.grid(row=0, column=1, padx=8)
        tk.Label(settings_row, text="张", font=FONT, bg=COLORS["panel"]).grid(row=0, column=2, sticky="w")
        tk.Label(settings_row, text="掌握度阈值：", font=FONT, bg=COLORS["panel"]).grid(row=1, column=0, sticky="w", pady=6)
        threshold_ent = tk.Entry(settings_row, font=FONT, width=10)
        threshold_ent.insert(0, str(settings.get("mastery_threshold", train.MASTERY_THRESHOLD)))
        threshold_ent.grid(row=1, column=1, padx=8)
        tk.Label(settings_row, text="%（默认 80%）", font=FONT, bg=COLORS["panel"]).grid(row=1, column=2, sticky="w")
        tk.Label(settings_row, text="提醒时间：", font=FONT, bg=COLORS["panel"]).grid(row=2, column=0, sticky="w", pady=6)
        reminder_time_ent = tk.Entry(settings_row, font=FONT, width=10)
        reminder_time_ent.insert(0, str(settings.get("reminder_time", "08:30")))
        reminder_time_ent.grid(row=2, column=1, padx=8)
        tk.Label(settings_row, text="HH:MM", font=FONT, bg=COLORS["panel"]).grid(row=2, column=2, sticky="w")
        tk.Label(settings_row, text="提醒频率：", font=FONT, bg=COLORS["panel"]).grid(row=3, column=0, sticky="w", pady=6)
        reminder_frequency = ttk.Combobox(settings_row, values=("daily", "only_due", "off"), state="readonly", width=12)
        reminder_frequency.set(str(settings.get("reminder_frequency", "only_due")))
        reminder_frequency.grid(row=3, column=1, padx=8, sticky="w")
        tk.Label(settings_row, text="daily=每日，only_due=有到期卡时，off=关闭", font=("Microsoft YaHei", 10), bg=COLORS["panel"], fg=COLORS["muted"]).grid(row=3, column=2, sticky="w")
        tk.Label(settings_tab, text="掌握度由最近 10 次自评和记忆等级综合计算；低于 60%进入薄弱清单。",
                 font=FONT_SMALL, fg=COLORS["muted"], bg=COLORS["panel"]).pack(anchor="w", padx=18, pady=8)

        def save_training_settings():
            try:
                values = train.update_training_settings(
                    int(daily_ent.get()), int(threshold_ent.get()),
                    reminder_time=reminder_time_ent.get().strip(),
                    reminder_frequency=reminder_frequency.get(),
                )
            except ValueError:
                messagebox.showwarning("提示", "请输入有效的数字。", parent=win)
                return
            refresh_dashboard()
            refresh_reminder()
            messagebox.showinfo("已保存", f"训练设置已保存：每日 {values['daily_limit']} 张，掌握度阈值 {values['mastery_threshold']}%。", parent=win)

        tk.Button(settings_tab, text="保存训练设置", font=("Microsoft YaHei", 11, "bold"),
                  command=save_training_settings, **primary).pack(anchor="w", padx=18, pady=12, ipadx=8, ipady=4)

    # ---------- 6. 配置 ----------
    def open_config(self):
        win = _BaseWin("系统设置", 760, 760)
        cfg = load_config()
        txt = tk.Label(win, font=FONT_MONO, justify="left",
                       bg=COLORS["bg"], anchor="w")
        txt.pack(fill="x", padx=15, pady=8)
        info = [f"知识库目录：{cfg['knowledge_base']}", f"默认工具：{cfg['default_tool']}"]
        for key, t in cfg["ai_tools"].items():
            st = "✅启用" if t.get("enabled") and get_api_key(key, t) else "❌未配置"
            info.append(f"{t['name']}：{st}  模型={t.get('model')}")
        txt.config(text="\n".join(info))

        tk.Label(win, text="知识库目录：", font=FONT, bg=COLORS["bg"]).pack(pady=(6, 2))
        path_row = tk.Frame(win, bg=COLORS["bg"])
        path_row.pack(fill="x", padx=18)
        path_ent = tk.Entry(path_row, font=FONT)
        path_ent.insert(0, cfg["knowledge_base"])
        path_ent.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def _choose_kb():
            chosen = filedialog.askdirectory(title="选择 Obsidian 知识库目录", parent=win)
            if chosen:
                path_ent.delete(0, "end")
                path_ent.insert(0, chosen)

        tk.Button(path_row, text="选择", font=FONT, command=_choose_kb,
                  bg="#ffffff", fg=COLORS["navy"], relief="flat").pack(side="right")

        tk.Label(win, text="配置 API Key（可选）：", font=FONT, bg=COLORS["bg"]).pack(pady=(12, 6))
        tool_var = tk.StringVar(value="doubao")
        row = tk.Frame(win, bg=COLORS["bg"])
        row.pack()
        for key, t in cfg["ai_tools"].items():
            tk.Radiobutton(row, text=t["name"], variable=tool_var, value=key,
                           font=FONT, bg=COLORS["bg"]).pack(side="left", padx=6)
        key_ent = tk.Entry(win, font=FONT, width=55, show="*")
        key_ent.pack(pady=6)

        def _save():
            key = tool_var.get()
            ak = key_ent.get().strip()
            c = load_config()
            kb_path = path_ent.get().strip()
            if not kb_path:
                messagebox.showwarning("提示", "知识库目录不能为空。", parent=win)
                return
            c["knowledge_base"] = kb_path
            if ak:
                c["ai_tools"][key]["api_key"] = ak
                c["ai_tools"][key]["enabled"] = True
            check = validate_config(c)
            if not check["ok"]:
                messagebox.showerror("配置校验失败", "\n".join(check["errors"]), parent=win)
                return
            save_config(c)
            proc.ensure_knowledge_base()
            messagebox.showinfo("成功", "系统配置已保存，知识库目录已初始化。", parent=win)

        tk.Button(win, text="保存配置", font=FONT, command=_save,
                  bg=COLORS["navy"], fg="#fff", relief="flat", pady=8).pack(pady=10)
        tk.Label(win, text="API Key 可通过 WESTGAS_DEEPSEEK_API_KEY / WESTGAS_DOUBAO_API_KEY 环境变量提供，避免写入配置文件。", font=("Microsoft YaHei", 10),
                 bg=COLORS["bg"], fg="#888").pack()

        tk.Label(win, text="知识库周期治理", font=("Microsoft YaHei", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["navy"]).pack(pady=(16, 4))
        tk.Label(win, text="每日增量只生成待审核草稿；将草稿中的 review_status 改为 approved 后再发布。",
                 font=("Microsoft YaHei", 10), bg=COLORS["bg"], fg="#666").pack()
        job_row = tk.Frame(win, bg=COLORS["bg"])
        job_row.pack(pady=7)

        job_log = scrolledtext.ScrolledText(win, height=7, font=FONT_MONO)
        job_log.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        def show_job(value):
            job_log.insert("end", value + "\n")
            job_log.see("end")

        def run_job(kind):
            show_job(f"正在运行：{kind} ...")
            def work():
                try:
                    result = maintenance.run_job(kind)
                    if isinstance(result, dict):
                        summary = []
                        for key, value in result.items():
                            if isinstance(value, list):
                                summary.append(f"{key}={len(value)}")
                            elif isinstance(value, str):
                                summary.append(f"{key}={value}")
                        message = "✅ " + kind + " 完成：" + "，".join(summary)
                    else:
                        message = f"✅ {kind} 完成：{result}"
                except Exception as exc:
                    message = f"❌ {kind} 失败：{exc}"
                win.run_on_ui(lambda: show_job(message))
            win.run_async(work)

        for label, kind in [("立即增量整理 / 重试失败", "daily"), ("生成周报", "weekly"),
                            ("生成月报", "monthly"), ("发布已审核更新", "publish")]:
            tk.Button(job_row, text=label, font=FONT_SMALL,
                      command=lambda value=kind: run_job(value), bg="#ffffff",
                      fg=COLORS["navy"], relief="flat", padx=8, pady=5).pack(side="left", padx=3)
        review_row = tk.Frame(win, bg=COLORS["bg"])
        review_row.pack(pady=3)
        tk.Button(review_row, text="打开待审核更新", font=FONT_SMALL,
                  command=self.open_update_review, bg="#dff0e5", fg="#1f6b3b",
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)

        def create_backup():
            try:
                path = backup.create_backup(proc.get_kb_path())
                show_job(f"✅ 知识库备份已创建：{path}")
            except Exception as exc:
                show_job(f"❌ 备份失败：{exc}")

        def restore_backup():
            choices = backup.list_backups(proc.get_kb_path())
            path = filedialog.askopenfilename(
                title="选择知识库备份 ZIP",
                initialdir=os.path.dirname(choices[0]) if choices else proc.get_kb_path(),
                filetypes=[("知识库备份", "*.zip"), ("所有文件", "*.*")],
                parent=win,
            )
            if not path:
                return
            if not messagebox.askyesno("确认恢复", "恢复前会自动创建当前知识库安全备份，继续吗？", parent=win):
                return
            try:
                safety = backup.restore_backup(proc.get_kb_path(), path)
                show_job(f"✅ 恢复完成，恢复前安全备份：{safety}")
            except Exception as exc:
                show_job(f"❌ 恢复失败：{exc}")

        backup_row = tk.Frame(win, bg=COLORS["bg"])
        backup_row.pack(pady=3)
        tk.Button(backup_row, text="创建知识库备份", font=FONT_SMALL,
                  command=create_backup, bg="#ffffff", fg=COLORS["navy"],
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)
        tk.Button(backup_row, text="恢复知识库备份", font=FONT_SMALL,
                  command=restore_backup, bg="#ffffff", fg="#7a3434",
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)
        schedule_row = tk.Frame(win, bg=COLORS["bg"])
        schedule_row.pack(pady=3)

        def manage_schedule(install):
            action = "安装每日/每周/每月后台任务" if install else "移除本系统计划任务"
            if not messagebox.askyesno("确认", f"确定{action}吗？", parent=win):
                return
            def work():
                try:
                    result = scheduled_tasks.install_tasks() if install else scheduled_tasks.remove_tasks()
                    message = f"✅ {action}完成。"
                except Exception as exc:
                    message = f"❌ {action}失败：{exc}"
                win.run_on_ui(lambda: show_job(message))
            win.run_async(work)

        tk.Button(schedule_row, text="安装自动计划", font=FONT_SMALL,
                  command=lambda: manage_schedule(True), bg="#dff0e5", fg="#1f6b3b",
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)
        tk.Button(schedule_row, text="移除自动计划", font=FONT_SMALL,
                  command=lambda: manage_schedule(False), bg="#ffffff", fg="#7a3434",
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)
        tk.Button(schedule_row, text="查看计划状态", font=FONT_SMALL,
                  command=lambda: show_schedule_status(), bg="#ffffff", fg=COLORS["navy"],
                  relief="flat", padx=10, pady=5).pack(side="left", padx=4)

        def show_schedule_status():
            def work():
                try:
                    tasks = scheduled_tasks.list_tasks()
                    message = "计划任务状态：\n" + "\n".join(
                        f"{'已安装' if item['installed'] else '未安装'}：{item['name']}"
                        for item in tasks
                    )
                except Exception as exc:
                    message = f"❌ 查询计划状态失败：{exc}"
                win.run_on_ui(lambda: show_job(message))
            win.run_async(work)
        tk.Label(win, text="任务计划程序可调用：pythonw knowledge_job.py daily|weekly|monthly|publish",
                 font=FONT_MONO, bg=COLORS["bg"], fg="#777").pack(pady=(0, 10))


def main():
    App().mainloop()


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        try:
            maintenance.run_job(sys.argv[2])
        except Exception as exc:
            maintenance._append_log(f"后台任务失败 {sys.argv[2]}：{exc}")
            raise
    else:
        main()


