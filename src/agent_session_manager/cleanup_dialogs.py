"""Deletion confirmation and readable result dialogs."""
import tkinter as tk
from tkinter import simpledialog, ttk
from tkinter.scrolledtext import ScrolledText


class CleanupConfirmation(simpledialog.Dialog):
    def __init__(self, parent, prompt, phrase):
        self.prompt, self.phrase = prompt, phrase
        super().__init__(parent, "确认清理")

    def body(self, master):
        ttk.Label(master, text=self.prompt, wraplength=680, justify="left").pack(anchor="w", padx=12, pady=12)
        ttk.Label(master, text=f"请输入 {self.phrase} 确认：").pack(anchor="w", padx=12)
        self.entry = ttk.Entry(master, width=42)
        self.entry.pack(fill="x", padx=12, pady=8)
        self.error = tk.StringVar()
        ttk.Label(master, textvariable=self.error, foreground="#b42318", wraplength=680).pack(anchor="w", padx=12, pady=(0, 8))
        return self.entry

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="取消", command=self.cancel).pack(side="right", padx=8)
        ttk.Button(box, text="确认清理", command=self.ok).pack(side="right")
        box.pack(fill="x", padx=12, pady=12)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        if self.entry.get() != self.phrase:
            self.error.set(f"输入不匹配，请完整输入 {self.phrase}；尚未执行清理。")
            self.entry.selection_range(0, "end")
            return False
        return True

    def apply(self):
        self.result = True


def show_cleanup_result(parent, report, application):
    from .cli import format_bytes
    impact = report.impact
    checks = "通过" if not report.verification else "仍有残留，请查看详情"
    warnings = report.warnings + (["仍有待人工核对的产物。"] if report.unresolved_artifacts else [])
    window = tk.Toplevel(parent)
    window.title("清理完成 · " + application.label)
    window.transient(parent)
    window.geometry("820x620")
    window.minsize(640, 420)
    frame = ttk.Frame(window, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="清理完成" if not warnings else "清理完成，有保留事项", style="Title.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text=f"删除 {len(report.deleted_ids)} 项 · 复核：{checks}").grid(row=1, column=0, sticky="w", pady=(8, 16))
    text = ScrolledText(frame, wrap="word", font=getattr(parent, "body_font", "TkDefaultFont"), padx=14, pady=12)
    text.grid(row=2, column=0, sticky="nsew")
    frame.rowconfigure(2, weight=1)
    frame.columnconfigure(0, weight=1)
    lines = ["删除结果", f"  对话／侧边栏记录：{len(report.deleted_ids)} 项", f"  会话文件：{len(report.deleted_files)} 个", f"  专属产物文件：{report.artifact_files_deleted} 个", "", "索引与历史", f"  会话索引：{report.index_entries_removed} 条", f"  本地历史：{report.history_entries_removed} 条", f"  全局状态引用：{report.global_state_references_removed} 处", f"  数据库记录：{sum(sum(v.values()) for v in report.sqlite_rows_removed_all.values())} 条", "", "复核与保留", f"  已知本地引用复核：{checks}", f"  持久备份：{report.backup_dir or '未创建'}", "  项目文件、共享附件、已有导出和云端数据保留。"]
    if impact:
        lines += ["", "删除的数据量", f"  会话与专属产物：{format_bytes(impact.rollout_bytes + impact.artifact_bytes)}", "  此值不等于数据库立即释放的磁盘空间。"]
    if report.verification:
        lines += ["", "剩余引用"] + [f"  {k}：{v}" for k, v in report.verification.items()]
    if warnings:
        lines += ["", "保留事项"] + [f"  • {v}" for v in warnings]
    text.insert("1.0", "\n".join(lines));text.configure(state="disabled")
    ttk.Button(frame, text="关闭", command=window.destroy).grid(row=3, column=0, sticky="e", pady=(14, 0))
    window.update_idletasks();window.grab_set();window.focus_set()
    return window
