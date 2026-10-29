from __future__ import annotations

import json
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .cleanup_dialogs import CleanupConfirmation, show_cleanup_result
from . import __version__
from .backup import create_manual_backup, verify_backup
from .backup_manager import delete_backup, list_backups, retention_candidates
from .cli import format_bytes
from .codex_controller import CodexController
from .conversation_reader import ConversationDetails, read_conversation
from .applications import get_application
from .executor import CleanupExecutor, recover_transaction, restore_backup
from .impact_analyzer import analyze_deletion
from .models import SessionStatus
from .orphan_cleaner import execute_orphan_cleanup, scan_orphans
from .maintenance import compact_log_databases, log_database_stats
from .planner import build_plan
from .platforms import get_platform
from .process_guard import running_codex_processes
from .projects import ProjectInfo, project_info
from .recovery import pending_transactions
from .scanner import SessionScanner, default_codex_home
from .settings import AppSettings, load_settings, save_settings


WINDOW_TITLE = "智能体会话管理器 · Agent Session Manager"


def responsive_font_size(width: int, height: int) -> int:
    """Scale with the window's usable dimensions; keep small windows readable."""
    return max(12, min(28, round(12 * min(width / 1420, height / 900))))


def display_time(value: str) -> str:
    if not value:
        return "—"
    try:
        text = str(value)
        if text.replace(".", "", 1).isdigit():
            stamp = float(text)
            if stamp > 100_000_000_000:
                stamp /= 1000
            date = datetime.fromtimestamp(stamp).astimezone()
        else:
            date = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
        return date.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return str(value)


def open_directory(path: Path) -> None:
    subprocess.Popen(get_platform().directory_command(path))


STATUS_LABELS = {
    SessionStatus.USER_ROOT: "用户对话",
    SessionStatus.ATTACHED: "附属子智能体",
    SessionStatus.ORPHAN: "孤儿子智能体",
    SessionStatus.UNLINKED_INTERNAL: "无父内部线程",
    SessionStatus.LOCAL_GHOST: "侧边栏残留（无本地内容）",
    SessionStatus.PAGINATED: "分页历史（只读）",
    SessionStatus.CLOUD_CATALOG: "云端目录（只读）",
    SessionStatus.UNKNOWN: "未知",
}

ORPHAN_CATEGORY_LABELS = {
    "attachment": "孤立附件",
    "visualizations": "可视化产物",
    "generated_images": "生成图片",
    "browser": "浏览器会话",
    "memories": "记忆产物",
    ".tmp": "临时文件",
    "tmp": "临时文件",
    "cache": "缓存",
    "computer-use": "计算机操作产物",
    "node_repl": "Node 运行产物",
}


def orphan_category_label(category: str) -> str:
    return ORPHAN_CATEGORY_LABELS.get(category, category)


def orphan_reason_label(reason: str) -> str:
    if reason.startswith("no surviving rollout references this attachment ID"):
        return "没有任何现存本地对话引用此附件"
    if reason.startswith("UUID is not present in any surviving local session or catalog record"):
        return "对应任务 ID 已不存在于本地会话或目录记录"
    if reason.startswith("manual review required; unsafe or unreadable tree"):
        return "包含目录联接、符号链接或无法安全读取，仅供查看"
    return "需要人工确认，程序不会自动删除"


def orphan_id_type_label(category: str) -> str:
    return "附件 ID" if category == "attachment" else "任务 ID"


def orphan_ownership_label(category: str) -> str:
    if category == "attachment":
        return "已检查全部现存 rollout，未发现任何消息或附件路径引用此附件 ID。"
    return "路径中的 UUID 是任务 ID；该 ID 不存在于本地 rollout、状态数据库或本地任务目录。"


def orphan_decision_label(item, deletable: bool, grace_days: int) -> str:
    if deletable:
        return f"可清理：最后修改时间已超过 {grace_days} 天保护期。"
    if item.reason.startswith("manual review required; unsafe or unreadable tree"):
        return "不自动清理：内部包含目录联接、符号链接或无法安全读取，递归删除可能越界。"
    try:
        protected_until = datetime.fromisoformat(item.modified_at) + timedelta(days=grace_days)
        until_text = protected_until.astimezone().strftime("%Y-%m-%d %H:%M")
        return f"暂不清理：尚在 {grace_days} 天保护期内，保护至 {until_text}。"
    except (TypeError, ValueError):
        return "暂不清理：修改时间无法确认，需要人工检查。"


def resolve_orphan_result_path(codex_home: Path, relative_path: str) -> Path:
    root = codex_home.expanduser().resolve()
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("结果路径不是安全的相对路径。")
    target = (root / relative).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("结果路径超出所选 .codex 目录。") from exc
    if target == root:
        raise ValueError("拒绝打开 .codex 根目录作为残留项目。")
    return target


def orphan_location_command(codex_home: Path, relative_path: str) -> list[str]:
    target = resolve_orphan_result_path(codex_home, relative_path)
    return get_platform().directory_command(target, reveal=True)


class OrphanResultsWindow(tk.Toplevel):
    def __init__(self, parent: "CleanerApp", result, clean_callback) -> None:
        super().__init__(parent)
        self.parent = parent
        self.result = result
        self.clean_callback = clean_callback
        self.title("历史残留管理")
        width = min(1180, max(860, self.winfo_screenwidth() - 160))
        height = min(760, max(580, self.winfo_screenheight() - 180))
        self.geometry(f"{width}x{height}")
        self.minsize(820, 540)
        self.transient(parent)

        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="历史残留扫描结果", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            container,
            text=(
                f"可安全清理 {len(result.candidates)} 项 · {result.file_count} 个文件 · "
                f"预计释放 {format_bytes(result.total_bytes)}    |    "
                f"仅报告 {len(result.review_only)} 项    |    保留备份 {len(result.retained_backups)} 处"
            ),
            foreground="#444",
        ).pack(anchor="w", pady=(5, 4))
        ttk.Label(
            container,
            text=(
                f"规则：仅列入超过 {result.grace_days} 天、无现存任务引用且位于受管理 .codex 目录内的产物。"
                "项目文件、近期产物、目录联接和备份不会删除。"
            ),
            foreground="#666",
            wraplength=1080,
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            container,
            text=(
                "判断方式：附件目录中的 UUID 是附件 ID，程序检查它是否仍被任何现存 rollout 引用；"
                "可视化、生成图片和浏览器产物中的 UUID 是任务 ID，程序检查该任务是否仍存在。"
            ),
            foreground="#444",
            wraplength=1080,
        ).pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        self._candidate_tab(
            notebook, "可安全清理", result.candidates,
            "这些项目满足自动清理规则；双击一行或点击“打开所在位置”可在资源管理器中定位。",
            deletable=True,
        )
        self._candidate_tab(
            notebook, "暂不清理或需人工处理", result.review_only,
            f"本页项目不包含在本次 CLEAN-{len(result.candidates)} 清理清单中。"
            "请查看“处理决定”了解保护期或安全阻止原因。",
            deletable=False,
        )
        state_frame = ttk.Frame(notebook, padding=12)
        notebook.add(state_frame, text=f"已删对话状态引用（{len(result.state_reference_ids)}）")
        ttk.Label(state_frame, text="以下 ID 已由本工具的成功删除日志确认，且不再对应现存对话或数据库历史。\n只修复已核实布局中的状态引用；未知引用不会自动清理。", wraplength=1000, justify="left").pack(anchor="w", pady=(0, 10))
        state_text = tk.Text(state_frame, wrap="none", height=8, font=getattr(parent, "body_font", "TkDefaultFont"))
        state_text.pack(fill="both", expand=True)
        state_text.insert("1.0", "\n".join(result.state_reference_ids) or "没有发现已确认删除的对话状态残留。")
        state_text.configure(state="disabled")
        self._backup_tab(notebook, result.retained_backups)

        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(
            footer,
            text=f"已确认删除的对话另有 {len(result.state_reference_ids)} 个 ID 遗留状态引用；日志另有 {len(result.operation_log_ids)} 个失效 ID。",
            foreground="#666", wraplength=1000,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Button(footer, text="关闭", command=self.destroy).grid(row=1, column=2, sticky="e")
        self.clean_button = ttk.Button(
            footer,
            text=f"清理 {len(result.candidates)} 项产物及 {len(result.state_reference_ids)} 项引用",
            style="Danger.TButton",
            command=self._confirm_clean,
            state="normal" if result.candidates or result.operation_log_ids or result.state_reference_ids else "disabled",
        )
        self.clean_button.grid(row=1, column=1, sticky="e", padx=(0, 8))
        footer.columnconfigure(0, weight=1)
        notebook.pack_forget()
        notebook.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after_idle(self._center_on_parent)

    def _center_on_parent(self) -> None:
        self.update_idletasks()
        x = max(0, self.parent.winfo_rootx() + (self.parent.winfo_width() - self.winfo_width()) // 2)
        y = max(0, self.parent.winfo_rooty() + (self.parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")
        self.focus_set()

    def _candidate_tab(
        self, notebook: ttk.Notebook, title: str, items, note: str, *, deletable: bool
    ) -> None:
        frame = ttk.Frame(notebook, padding=8)
        notebook.add(frame, text=f"{title}（{len(items)}）")
        ttk.Label(frame, text=note, foreground="#666").grid(row=0, column=0, sticky="w", pady=(0, 6))
        columns = ("category", "id_type", "ownership", "decision", "modified", "size", "path")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "category": "类型", "id_type": "ID 类型", "ownership": "归属判断",
            "decision": "处理决定", "modified": "最后修改时间", "size": "大小",
            "path": "相对路径（双击定位）",
        }
        widths = {
            "category": 105, "id_type": 75, "ownership": 360, "decision": 340,
            "modified": 145, "size": 80, "path": 430,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column, width=widths[column], minwidth=70,
                stretch=column in {"ownership", "decision", "path"},
            )
        for item in items:
            modified = item.modified_at.replace("T", " ")[:19] if item.modified_at else "未知"
            tree.insert("", "end", values=(
                orphan_category_label(item.category), orphan_id_type_label(item.category),
                orphan_ownership_label(item.category),
                orphan_decision_label(item, deletable, self.result.grace_days),
                modified, format_bytes(item.size), item.relative_path,
            ))
        ttk.Button(
            frame, text="打开所在位置", command=lambda: self._open_selected(tree)
        ).grid(row=0, column=1, sticky="e", padx=(8, 0), pady=(0, 6))
        tree.bind("<Double-1>", lambda _event: self._open_selected(tree))
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

    def _open_selected(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("请选择项目", "请先选择一行，再打开所在位置。", parent=self)
            return
        relative_path = str(tree.set(selected[0], "path") or "")
        try:
            target = resolve_orphan_result_path(self.result.codex_home, relative_path)
            if not target.exists():
                messagebox.showwarning(
                    "项目已不存在",
                    "该项目可能已被其他程序删除或移动，请重新扫描历史残留。",
                    parent=self,
                )
                return
            subprocess.Popen(orphan_location_command(self.result.codex_home, relative_path))
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法打开所在位置", str(exc), parent=self)

    def _backup_tab(self, notebook: ttk.Notebook, backups: list[str]) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text=f"保留的备份（{len(backups)}）")
        ttk.Label(
            frame,
            text="以下位置可能保存可恢复副本。本次历史残留清理不会删除它们。",
            foreground="#666",
        ).pack(anchor="w", pady=(0, 8))
        text = tk.Text(frame, wrap="word", height=10, padx=10, pady=8)
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(backups) if backups else "没有发现受管理备份。")
        text.configure(state="disabled")

    def _confirm_clean(self) -> None:
        phrase = f"CLEAN-{len(self.result.candidates) + len(self.result.state_reference_ids)}"
        prompt = (f"将清理 {len(self.result.candidates)} 项产物、{self.result.file_count} 个文件，"
                  f"预计删除数据 {format_bytes(self.result.total_bytes)}。\n"
                  f"另修复 {len(self.result.state_reference_ids)} 个已删除对话的状态引用。\n\n"
                  "项目文件、现存对话和已有备份保留。")
        if not CleanupConfirmation(self, prompt, phrase).result:
            return
        self.destroy()
        self.clean_callback(self.result)


class CleanerApp(tk.Tk):
    def __init__(self, codex_home: Path, application: str = "codex", session_root: Path | None = None):
        super().__init__()
        self.application = get_application(application)
        self.initial_session_root = session_root
        self.title(WINDOW_TITLE)
        self.geometry(f"{min(1420, self.winfo_screenwidth() - 80)}x{min(900, self.winfo_screenheight() - 100)}")
        self.minsize(1060, 680)
        self.codex_home = codex_home.expanduser()
        self.settings = load_settings()
        self.scan_result = None
        self.current_id: str | None = None
        self._metadata: dict[str, object] = {}
        self.project_label_to_key: dict[str, str | None] = {"全部项目": None}
        self.project_node_ids: dict[str, set[str]] = {}
        self.group_node_ids: dict[str, set[str]] = {}
        self.platform = get_platform()
        self._build_style()
        self._build_ui()
        self._font_resize_job = None
        self._current_font_size = 12
        self.bind("<Configure>", self._schedule_font_resize, add="+")
        self.after_idle(self._resize_fonts)
        self.after(100, self.refresh)
        self.after(1000, self._refresh_process_status)

    def _build_style(self) -> None:
        if self.platform.TK_SCALING is not None:
            self.tk.call("tk", "scaling", self.platform.TK_SCALING)
        families = set(tkfont.families(self))
        preferred = self.platform.PREFERRED_FONTS
        self.ui_family = next((name for name in preferred if name in families), tkfont.nametofont("TkDefaultFont").actual("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkSmallCaptionFont", "TkCaptionFont", "TkIconFont"):
            tkfont.nametofont(name).configure(family=self.ui_family, size=12)
        self.body_font = tkfont.Font(self, family=self.ui_family, size=13)
        self.role_font = tkfont.Font(self, family=self.ui_family, size=12, weight="bold")
        self.title_font = tkfont.Font(self, family=self.ui_family, size=16, weight="bold")
        self.option_add("*Font", "TkDefaultFont")
        style = ttk.Style(self)
        if self.platform.PREFERRED_THEME in style.theme_names():
            style.theme_use(self.platform.PREFERRED_THEME)
        style.configure("Treeview", font="TkDefaultFont", rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + 10)
        style.configure("Treeview.Heading", font="TkHeadingFont")
        style.configure("Danger.TButton", foreground="#9f1d1d")
        style.configure("Title.TLabel", font=self.title_font)

    def _schedule_font_resize(self, event) -> None:
        if event.widget is not self:
            return
        if self._font_resize_job is not None:
            self.after_cancel(self._font_resize_job)
        self._font_resize_job = self.after(150, self._resize_fonts)

    def _resize_fonts(self) -> None:
        self._font_resize_job = None
        self._apply_font_scale(self.winfo_width(), self.winfo_height())

    def _apply_font_scale(self, width: int, height: int) -> None:
        size = responsive_font_size(width, height)
        if size == self._current_font_size:
            return
        self._current_font_size = size
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkSmallCaptionFont", "TkCaptionFont", "TkIconFont"):
            tkfont.nametofont(name).configure(size=size)
        self.body_font.configure(size=size + 1)
        self.role_font.configure(size=size, weight="bold")
        self.title_font.configure(size=round(size * 4 / 3), weight="bold")
        line_height = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style(self).configure("Treeview", rowheight=line_height + round(size * 0.8))
        self.messages_text.configure(spacing1=round(size / 4), spacing3=round(size / 2))
        for column, width in (("#0", 300), ("project", 140), ("status", 110), ("date", 145), ("size", 75), ("children", 50)):
            self.tree.column(column, width=round(width * size / 12))

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(12, 12, 12, 6))
        top.pack(fill="x")
        self.application_var = tk.StringVar(value=self.application.id)
        self.application_combo = ttk.Combobox(top, textvariable=self.application_var, values=("codex", "dsh"), state="readonly", width=8)
        self.application_combo.pack(side="left", padx=(0, 8))
        self.application_combo.bind("<<ComboboxSelected>>", self._change_application)
        ttk.Label(top, text="数据目录:").pack(side="left")
        self.home_var = tk.StringVar(value=str(self.codex_home))
        ttk.Entry(top, textvariable=self.home_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="选择", command=self.choose_home).pack(side="left")
        storage = ttk.Frame(self, padding=(12, 0, 12, 6))
        storage.pack(fill="x")
        ttk.Label(storage, text="DSH 会话根目录（可选）:").pack(side="left")
        self.session_root_var = tk.StringVar(value=str(self.initial_session_root or ""))
        self.session_root_entry = ttk.Entry(storage, textvariable=self.session_root_var)
        self.session_root_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.session_root_entry.configure(state="normal" if self.application.id == "dsh" else "disabled")
        toolbar = ttk.Frame(self, padding=(12, 0, 12, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="重新扫描", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="设置", command=self.open_settings).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="备份管理", command=self.open_backup_manager).pack(side="left", padx=(8, 0))
        self.orphan_button = ttk.Button(toolbar, text="历史残留清理", command=self.scan_legacy_orphans, state="normal" if self.application.id == "codex" else "disabled")
        self.orphan_button.pack(side="left", padx=(8, 0))
        self.recovery_button = ttk.Button(toolbar, text="事务恢复", command=self.open_transaction_recovery, state="normal" if self.application.id == "codex" else "disabled")
        self.recovery_button.pack(side="left", padx=(8, 0))

        filters = ttk.Frame(self, padding=(12, 4, 12, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="搜索:").pack(side="left")
        self.query_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.query_var, width=20)
        search.pack(side="left", padx=(6, 12))
        search.bind("<Return>", lambda _event: self._render_tree())
        self.query_var.trace_add("write", lambda *_args: self.after(180, self._render_tree))
        ttk.Label(filters, text="类型:").pack(side="left")
        self.kind_var = tk.StringVar(value="全部")
        kind = ttk.Combobox(
            filters,
            textvariable=self.kind_var,
            state="readonly",
            width=14,
            values=("全部", "用户对话", "附属子智能体", "孤儿子智能体", "无父内部线程",
                    "本地幽灵记录", "云端目录（只读）", "未知"),
        )
        kind.pack(side="left", padx=6)
        kind.bind("<<ComboboxSelected>>", lambda _event: self._render_tree())
        ttk.Label(filters, text="项目:").pack(side="left", padx=(8, 0))
        self.project_var = tk.StringVar(value="全部项目")
        self.project_combo = ttk.Combobox(
            filters,
            textvariable=self.project_var,
            state="readonly",
            width=18,
            values=("全部项目",),
        )
        self.project_combo.pack(side="left", padx=6)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_tree())
        tools = ttk.Frame(self, padding=(12, 0, 12, 6))
        tools.pack(fill="x")
        ttk.Button(tools, text="导出全部", command=lambda: self.manual_backup(all_sessions=True)).pack(side="right")
        self.compact_button = ttk.Button(tools, text="释放数据库空闲空间", command=self.compact_logs, state="normal" if self.application.id == "codex" else "disabled")
        self.compact_button.pack(side="right", padx=(0, 8))
        self.process_var = tk.StringVar(value="正在检查 Codex 状态…")
        ttk.Label(tools, textvariable=self.process_var).pack(side="right", padx=14)

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12)
        left = ttk.Frame(pane, width=600)
        left.grid_propagate(False)
        right = ttk.Frame(pane, width=600, padding=(12, 2, 0, 0))
        right.pack_propagate(False)
        pane.add(left, weight=1)
        pane.add(right, weight=1)

        columns = ("project", "status", "date", "size", "children")
        selection_bar = ttk.Frame(left)
        selection_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(selection_bar, text="选择全部结果", command=self.select_all_visible).pack(side="left")
        ttk.Button(selection_bar, text="反选", command=self.invert_visible_selection).pack(side="left", padx=6)
        ttk.Button(selection_bar, text="清除选择", command=lambda: self.tree.selection_remove(self.tree.selection())).pack(side="left")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", selectmode="extended", height=10)
        self.tree.heading("#0", text="标题")
        self.tree.heading("project", text="项目")
        self.tree.heading("status", text="类型")
        self.tree.heading("date", text="更新时间")
        self.tree.heading("size", text="大小")
        self.tree.heading("children", text="子项")
        self.tree.column("#0", width=300, minwidth=180)
        self.tree.column("project", width=140, minwidth=90)
        self.tree.column("status", width=110)
        self.tree.column("date", width=145)
        self.tree.column("size", width=75, anchor="e")
        self.tree.column("children", width=50, anchor="center")
        scroll_y = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll_y.grid(row=1, column=1, sticky="ns")
        scroll_x.grid(row=2, column=0, sticky="ew")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        header = ttk.Frame(right)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="请选择一个会话")
        title_label = ttk.Label(header, textvariable=self.title_var, style="Title.TLabel", wraplength=620)
        title_label.pack(anchor="w")
        self.facts_var = tk.StringVar(value="扫描后可查看消息、依赖关系与元数据。")
        facts_label = ttk.Label(header, textvariable=self.facts_var, foreground="#555", wraplength=620)
        facts_label.pack(anchor="w", pady=(5, 8))
        right.bind("<Configure>", lambda event: (title_label.configure(wraplength=max(100, event.width - 24)), facts_label.configure(wraplength=max(100, event.width - 24))))

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(0, 8))
        self.rename_button = ttk.Button(actions, text="重命名", command=self.rename_selected, state="disabled")
        self.rename_button.pack(side="left")
        self.archive_button = ttk.Button(actions, text="归档", command=self.toggle_archive, state="disabled")
        self.archive_button.pack(side="left", padx=6)
        ttk.Button(actions, text="导出所选", command=self.manual_backup).pack(side="left")
        delete_actions = ttk.Frame(right)
        delete_actions.pack(fill="x", pady=(0, 8))
        ttk.Button(delete_actions, text="删除会话", style="Danger.TButton", command=self.delete_selected).pack(side="right")

        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True)
        messages_tab = ttk.Frame(tabs)
        metadata_tab = ttk.Frame(tabs)
        tabs.add(messages_tab, text="对话内容")
        tabs.add(metadata_tab, text="元数据与依赖")
        self.messages_text = tk.Text(messages_tab, wrap="word", state="disabled", width=1, height=1, padx=12, pady=10, font=self.body_font, spacing1=3, spacing3=5)
        msg_scroll = ttk.Scrollbar(messages_tab, orient="vertical", command=self.messages_text.yview)
        self.messages_text.configure(yscrollcommand=msg_scroll.set)
        self.messages_text.pack(side="left", fill="both", expand=True)
        msg_scroll.pack(side="right", fill="y")
        self.messages_text.tag_configure("role", font=self.role_font, foreground="#245a9b")
        self.messages_text.tag_configure("meta", foreground="#777")
        self.metadata_text = tk.Text(metadata_tab, wrap="none", state="disabled", width=1, height=1, padx=12, pady=10)
        meta_y = ttk.Scrollbar(metadata_tab, orient="vertical", command=self.metadata_text.yview)
        meta_x = ttk.Scrollbar(metadata_tab, orient="horizontal", command=self.metadata_text.xview)
        self.metadata_text.configure(yscrollcommand=meta_y.set, xscrollcommand=meta_x.set)
        self.metadata_text.grid(row=0, column=0, sticky="nsew")
        meta_y.grid(row=0, column=1, sticky="ns")
        meta_x.grid(row=1, column=0, sticky="ew")
        metadata_tab.rowconfigure(0, weight=1)
        metadata_tab.columnconfigure(0, weight=1)

        bottom = ttk.Frame(self, padding=12)
        bottom.pack(fill="x")
        self.summary_var = tk.StringVar(value="准备扫描")
        ttk.Label(bottom, textvariable=self.summary_var).pack(anchor="w")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate")
        self.progress.pack(fill="x", pady=(6, 0))

    def _change_application(self, _event=None) -> None:
        self.application = get_application(self.application_var.get())
        if self.application.id == "dsh" and self.settings.dsh_home:
            self.home_var.set(self.settings.dsh_home)
        else:
            self.home_var.set(str(self.application.default_home()))
        self.session_root_var.set(self.settings.dsh_session_root if self.application.id == "dsh" else "")
        self.session_root_entry.configure(state="normal" if self.application.id == "dsh" else "disabled")
        for button in (self.orphan_button, self.recovery_button, self.compact_button):
            button.configure(state="normal" if self.application.id == "codex" else "disabled")
        self.current_id = None
        self.scan_result = None
        self.tree.delete(*self.tree.get_children())
        self._set_text(self.messages_text, "")
        self._set_text(self.metadata_text, "")
        self.rename_button.configure(state="disabled")
        self.archive_button.configure(state="disabled")
        self.refresh()

    def choose_home(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.home_var.get(), title="选择应用数据目录")
        if selected:
            self.home_var.set(selected)
            self.refresh()

    def _busy(self, value: bool, message: str = "") -> None:
        self.progress.start(10) if value else self.progress.stop()
        self.application_combo.configure(state="disabled" if value else "readonly")
        if message:
            self.summary_var.set(message)

    def _run_async(self, message: str, work, done) -> None:
        self._busy(True, message)

        def target() -> None:
            try:
                result = work()
                self.after(0, lambda: (self._busy(False), done(result)))
            except Exception as exc:
                self.after(0, lambda exc=exc: self._error("操作失败", exc))

        threading.Thread(target=target, daemon=True).start()

    def refresh(self) -> None:
        home = Path(self.home_var.get()).expanduser()
        application = self.application
        session_root = Path(self.session_root_var.get()).expanduser() if application.id == "dsh" and self.session_root_var.get().strip() else None
        self._run_async("正在只读扫描…", lambda: application.scan(home, session_root), self._render)

    def _render(self, scan) -> None:
        if scan.application != self.application.id or scan.codex_home != Path(self.home_var.get()).expanduser().resolve():
            return
        self.scan_result = scan
        self.codex_home = scan.codex_home
        self._refresh_project_filter()
        self._render_tree()
        counts = {status: len(scan.ids_with_status(status)) for status in SessionStatus}
        self.summary_var.set(
            f"总计 {len(scan.sessions)} | 用户 {counts[SessionStatus.USER_ROOT]} | "
            f"附属 {counts[SessionStatus.ATTACHED]} | 孤儿 {counts[SessionStatus.ORPHAN]} | "
            f"内部 {counts[SessionStatus.UNLINKED_INTERNAL]} | "
            f"幽灵 {counts[SessionStatus.LOCAL_GHOST]} | 云端 {counts[SessionStatus.CLOUD_CATALOG]} | "
            f"项目 {self._user_project_count()} | SQLite {scan.sqlite_integrity}"
        )
        if any(issue.blocking for issue in scan.issues):
            details = "\n".join(
                f"{issue.code}: {issue.message}\n{issue.path}"
                for issue in scan.issues if issue.blocking
            )
            messagebox.showwarning("发现阻止性问题", f"删除功能将拒绝执行：\n\n{details[:4000]}")

    def _matches(self, session_id: str) -> bool:
        if not self.scan_result:
            return False
        record = self.scan_result.sessions[session_id]
        status = self.scan_result.status_by_id[session_id]
        selected_kind = self.kind_var.get()
        if selected_kind != "全部" and STATUS_LABELS[status] != selected_kind:
            return False
        selected_project = self.project_label_to_key.get(self.project_var.get())
        if selected_project is not None and self._project(record).key != selected_project:
            return False
        query = self.query_var.get().strip().casefold()
        if not query:
            return True
        haystack = "\n".join((record.id, record.title, record.cwd, str(record.path))).casefold()
        return query in haystack

    @staticmethod
    def _project(record_or_cwd) -> ProjectInfo:
        if hasattr(record_or_cwd, "cwd"):
            record = record_or_cwd
            if record.project_id and record.project_name:
                return ProjectInfo(
                    key=record.project_id,
                    name=record.project_name,
                    path=record.project_path or record.cwd,
                    temporary=False,
                )
            return project_info(record.cwd)
        return project_info(str(record_or_cwd))

    def _root_ids(self, status: SessionStatus) -> list[str]:
        if not self.scan_result:
            return []
        ids = self.scan_result.ids_with_status(status)
        if status == SessionStatus.ORPHAN:
            ids = [sid for sid in ids if self.scan_result.parent_by_child.get(sid) not in self.scan_result.sessions]
        return ids

    def _user_project_count(self) -> int:
        if not self.scan_result:
            return 0
        return len({self._project(self.scan_result.sessions[sid]).key for sid in self._root_ids(SessionStatus.USER_ROOT)})

    def _refresh_project_filter(self) -> None:
        if not self.scan_result:
            return
        projects: dict[str, ProjectInfo] = {}
        for record in self.scan_result.sessions.values():
            info = self._project(record)
            projects.setdefault(info.key, info)
        name_counts: dict[str, int] = {}
        for info in projects.values():
            name_counts[info.name] = name_counts.get(info.name, 0) + 1
        mapping: dict[str, str | None] = {"全部项目": None}
        ordered = sorted(projects.values(), key=lambda item: (item.temporary, item.name.casefold(), item.path.casefold()))
        for info in ordered:
            label = info.name
            if name_counts[info.name] > 1:
                label = f"{info.name} — {info.path}"
            mapping[label] = info.key
        current_key = self.project_label_to_key.get(self.project_var.get())
        self.project_label_to_key = mapping
        self.project_combo.configure(values=tuple(mapping))
        matching_label = next((label for label, key in mapping.items() if key == current_key), "全部项目")
        self.project_var.set(matching_label)

    def _subtree_matches(self, session_id: str, visited: set[str] | None = None) -> bool:
        if not self.scan_result:
            return False
        visited = set() if visited is None else visited
        if session_id in visited:
            return False
        visited.add(session_id)
        if self._matches(session_id):
            return True
        return any(self._subtree_matches(child, visited) for child in self.scan_result.children.get(session_id, set()))

    def _render_tree(self) -> None:
        if not self.scan_result or not hasattr(self, "tree"):
            return
        selected = self.current_id
        self.tree.delete(*self.tree.get_children())
        self.project_node_ids = {}
        self.group_node_ids = {}
        user_roots = [sid for sid in self._root_ids(SessionStatus.USER_ROOT) if self._subtree_matches(sid)]
        by_project: dict[str, tuple[ProjectInfo, list[str]]] = {}
        for session_id in user_roots:
            info = self._project(self.scan_result.sessions[session_id])
            by_project.setdefault(info.key, (info, []))[1].append(session_id)
        if by_project:
            self.tree.insert("", "end", iid="group:projects", text=f"项目 ({len(by_project)})", open=True)
            ordered_projects = sorted(by_project.values(), key=lambda item: (item[0].temporary, item[0].name.casefold(), item[0].path.casefold()))
            for index, (info, session_ids) in enumerate(ordered_projects):
                project_iid = f"project:{index}"
                self.project_node_ids[project_iid] = set(session_ids)
                self.tree.insert(
                    "group:projects", "end", iid=project_iid,
                    text=f"{info.name} ({len(session_ids)})",
                    values=(info.path or "未设置工作目录", "项目", "", "", ""),
                    open=True,
                )
                for session_id in sorted(session_ids):
                    self._insert_session(project_iid, session_id, set())
        groups = (
            (SessionStatus.ORPHAN, "group:orphan"),
            (SessionStatus.UNLINKED_INTERNAL, "group:internal"),
            (SessionStatus.LOCAL_GHOST, "group:ghost"),
            (SessionStatus.CLOUD_CATALOG, "group:cloud"),
            (SessionStatus.PAGINATED, "group:paginated"),
            (SessionStatus.UNKNOWN, "group:unknown"),
        )
        for status, group_id in groups:
            roots = self._root_ids(status)
            roots = [sid for sid in roots if self._subtree_matches(sid)]
            if not roots:
                continue
            self.tree.insert("", "end", iid=group_id, text=f"{STATUS_LABELS[status]} ({len(roots)})", open=True)
            self.group_node_ids[group_id] = set(roots)
            for session_id in roots:
                self._insert_session(group_id, session_id, set())
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def _insert_session(self, parent_iid: str, session_id: str, visited: set[str]) -> None:
        if not self.scan_result or self.tree.exists(session_id) or session_id in visited:
            return
        if not self._subtree_matches(session_id):
            return
        visited = set(visited)
        visited.add(session_id)
        record = self.scan_result.sessions[session_id]
        status = self.scan_result.status_by_id[session_id]
        children = sorted(self.scan_result.children.get(session_id, set()))
        project = self._project(record)
        self.tree.insert(
            parent_iid,
            "end",
            iid=session_id,
            text=(record.title or "(无标题)")[:160],
            values=(
                project.name,
                STATUS_LABELS[status],
                display_time(record.updated_at),
                format_bytes(record.size),
                len(children),
            ),
            open=bool(self.query_var.get()),
        )
        for child in children:
            self._insert_session(session_id, child, visited)

    def _selected_ids(self) -> list[str]:
        if not self.scan_result:
            return []
        selected: set[str] = set()
        for item in self.tree.selection():
            if item in self.scan_result.sessions:
                selected.add(item)
            selected.update(self.project_node_ids.get(item, set()))
            selected.update(self.group_node_ids.get(item, set()))
        roots: list[str] = []
        for session_id in sorted(selected):
            parent = self.scan_result.parent_by_child.get(session_id)
            visited: set[str] = set()
            while parent and parent not in visited and parent not in selected:
                visited.add(parent)
                parent = self.scan_result.parent_by_child.get(parent)
            if parent not in selected:
                roots.append(session_id)
        return roots

    def _visible_session_ids(self) -> list[str]:
        if not self.scan_result:
            return []
        result: list[str] = []
        def visit(parent: str = "") -> None:
            for item in self.tree.get_children(parent):
                if item in self.scan_result.sessions:
                    result.append(item)
                visit(item)
        visit()
        return result

    def select_all_visible(self) -> None:
        ids = [sid for sid in self._visible_session_ids()
               if self.scan_result.status_by_id[sid] != SessionStatus.CLOUD_CATALOG]
        if ids:
            self.tree.selection_set(ids)

    def invert_visible_selection(self) -> None:
        visible = {sid for sid in self._visible_session_ids()
                   if self.scan_result.status_by_id[sid] != SessionStatus.CLOUD_CATALOG}
        selected = {item for item in self.tree.selection() if item in visible}
        self.tree.selection_set(sorted(visible - selected))

    def _on_select(self, _event=None) -> None:
        ids = self._selected_ids()
        session_id = ids[0] if len(ids) == 1 else None
        self.current_id = session_id
        if not session_id or not self.scan_result:
            self.title_var.set(f"已选择 {len(ids)} 项" if ids else "请选择一个会话")
            self.facts_var.set("可备份或删除多个所选根节点；删除会自动包含所有后代。")
            self.rename_button.configure(state="disabled")
            self.archive_button.configure(state="disabled")
            return
        record = self.scan_result.sessions[session_id]
        status = self.scan_result.status_by_id[session_id]
        parent = self.scan_result.parent_by_child.get(session_id)
        children = sorted(self.scan_result.children.get(session_id, set()))
        project = self._project(record)
        self.title_var.set(record.title or "(无标题)")
        self.facts_var.set(
            f"{project.name} · {STATUS_LABELS[status]} · {'已归档' if record.archived else '未归档'} · "
            f"{format_bytes(record.size)} · 子项 {len(children)} · ID {session_id}"
        )
        editable = self.application.id == "codex" and status == SessionStatus.USER_ROOT and record.has_rollout
        self.rename_button.configure(state="normal" if editable else "disabled")
        self.archive_button.configure(state="normal" if editable else "disabled", text="取消归档" if record.archived else "归档")
        metadata = {
            "application": self.application.id,
            "id": record.id,
            "status": status.value,
            "kind": record.kind.value,
            "source": record.source,
            "thread_source": record.thread_source,
            "history_mode": record.history_mode,
            "project_id": record.project_id,
            "title": record.title,
            "archived": record.archived,
            "parent_id": parent,
            "child_ids": children,
            "cwd": record.cwd,
            "project": {"name": project.name, "path": project.path, "temporary": project.temporary},
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "path": str(record.path) if record.path else None,
            "paths": [str(path) for path in record.paths],
            "rollout_segments": record.segment_count,
            "size": record.size,
            "warnings": record.warnings,
        }
        self._metadata = metadata
        self._set_text(self.metadata_text, json.dumps(metadata, ensure_ascii=False, indent=2))
        if record.path is None:
            note = (
                "这是云端目录记录。本地工具只能查看目录信息，不能删除服务器上的对话。"
                if status == SessionStatus.CLOUD_CATALOG else
                ("这是侧边栏保留的对话 ID，不是磁盘目录。未找到本地会话文件。\n\n"
                 "来源为全局侧边栏且不存在数据库历史时，关闭 Codex 后可用“删除会话”清理引用；"
                 "其他目录缓存仍须核实，程序会说明拒绝原因。")
            )
            self._set_text(self.messages_text, note)
            return
        self._set_text(self.messages_text, "正在读取对话内容…")

        def done(details: ConversationDetails) -> None:
            if self.current_id != session_id:
                return
            self._render_messages(details)

        self._run_async(
            "正在读取对话…",
            lambda: self.application.read(record, include_internal=self.settings.show_internal_messages),
            done,
        )

    @staticmethod
    def _set_text(widget: tk.Text, value: str, tags: tuple[str, ...] = ()) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value, tags)
        widget.configure(state="disabled")

    def _render_messages(self, details: ConversationDetails) -> None:
        self.messages_text.configure(state="normal")
        self.messages_text.delete("1.0", "end")
        for message in details.messages:
            heading = f"{message.role.upper()}  {display_time(message.timestamp)}\n"
            self.messages_text.insert("end", heading, "role")
            self.messages_text.insert("end", message.text + "\n\n")
        if not details.messages:
            self.messages_text.insert("end", "未找到可显示的消息。")
        self.messages_text.configure(state="disabled")
        metadata = dict(self._metadata)
        metadata["event_count"] = details.event_count
        metadata["event_type_counts"] = details.event_type_counts
        metadata["invalid_json_lines"] = details.invalid_line_count
        if self.settings.show_internal_messages:
            metadata["session_meta"] = details.metadata
        self._set_text(self.metadata_text, json.dumps(metadata, ensure_ascii=False, indent=2))
        self.summary_var.set(f"已加载 {len(details.messages)} 条消息；无效 JSON 行 {details.invalid_line_count}")

    def rename_selected(self) -> None:
        if self.application.id != "codex":
            return
        if not self.current_id or not self.scan_result:
            return
        record = self.scan_result.sessions[self.current_id]
        value = simpledialog.askstring("重命名会话", "新标题（1-200 字符）:", initialvalue=record.title, parent=self)
        if value is None:
            return
        controller = CodexController(self.codex_home, self.settings.codex_binary)
        self._run_async("正在通过 Codex 重命名…", lambda: controller.rename(record.id, value), lambda _result: self.refresh())

    def toggle_archive(self) -> None:
        if self.application.id != "codex":
            return
        if not self.current_id or not self.scan_result:
            return
        record = self.scan_result.sessions[self.current_id]
        target = not record.archived
        controller = CodexController(self.codex_home, self.settings.codex_binary)
        label = "归档" if target else "取消归档"
        self._run_async(f"正在{label}…", lambda: controller.set_archived(record.id, target), lambda _result: self.refresh())

    def _backup_destination(self) -> Path | None:
        initial = self.settings.backup_directory or str(self.codex_home / "session_manager_backups")
        selected = filedialog.askdirectory(initialdir=initial, title="选择备份保存目录")
        return Path(selected) if selected else None

    def _backup_root(self) -> Path:
        return Path(self.settings.backup_directory).expanduser() if self.settings.backup_directory else self.codex_home / "session_manager_backups"

    def manual_backup(self, all_sessions: bool = False) -> None:
        if not self.scan_result:
            return
        ids = [] if all_sessions else self._selected_ids()
        if not all_sessions and not ids:
            messagebox.showinfo("没有选择", "请先选择至少一个会话。")
            return
        destination = self._backup_destination()
        if not destination:
            return
        self._run_async(
            "正在导出会话副本…",
            lambda: self.application.export(self.scan_result, selected_ids=ids, destination=destination),
            lambda result: messagebox.showinfo(
                "导出完成",
                f"会话导出包已创建：\n{result[0]}\n\n"
                "导出包用于保存副本，不能覆盖恢复完整应用状态。",
            ),
        )

    def delete_selected(self) -> None:
        if not self.scan_result:
            return
        selected = self._selected_ids()
        if not selected:
            messagebox.showinfo("没有选择", "请先选择要删除的会话。")
            return
        running = self.application.running(self.codex_home)
        if running:
            messagebox.showwarning(
                f"请先完全关闭 {self.application.label}",
                "查看、搜索和导出可以在应用运行时执行。\n\n"
                "删除会同时更新会话文件和本地索引，因此只有完全关闭应用后才能继续。\n"
                f"检测到：{', '.join(running)}",
            )
            return
        try:
            plan = self.application.plan(self.scan_result, selected)
        except Exception as exc:
            self._error("无法生成删除计划", exc)
            return
        try:
            impact = self.application.impact(self.scan_result, plan)
        except Exception as exc:
            self._error("无法核对清理范围", exc)
            return
        backup_enabled = self.settings.backup_before_delete
        phrase = (self.settings.confirmation_text.strip() or "DELETE").replace("{count}", str(len(plan.deletion_ids)))
        prompt = (
            f"将本地清理 {len(plan.deletion_ids)} 个会话（含附属子会话），"
            f"{plan.file_count} 个会话文件和 {impact.artifact_files} 个专属附件文件。\n"
            + ("会同步清理对应投影缓存、工作区归档和置顶引用。\n\n" if self.application.id == "dsh" else "会同步清理匹配的本地索引、历史记录及数据库引用。\n\n")
            +
            "共享附件、项目文件、已有导出和云端数据保留。\n"
            + (("先保留会话导出副本（不能覆盖恢复状态）。\n" if self.application.id == "dsh" else "先保留清理前快照。\n") if backup_enabled else ("不保留持久备份；同盘暂存仅用于失败回滚。\n" if self.application.id == "dsh" else "不保留持久备份；临时事务快照仅用于失败回滚。\n"))
            + f"\n输入 {phrase} 继续："
        )
        if not CleanupConfirmation(self, prompt.rsplit("\n输入 ", 1)[0], phrase).result:
            return
        self._run_async(
            "正在清理本地会话及引用…",
            lambda: self.application.execute(
                plan, persistent_backup=backup_enabled, backup_directory=self._backup_root()),
            self._delete_done,
        )

    def compact_logs(self) -> None:
        if self.application.id != "codex":
            return
        running = running_codex_processes()
        if running:
            messagebox.showwarning(
                "请先完全关闭 Codex",
                "释放数据库空间时必须完全关闭 Codex。\n检测到：" + ", ".join(running),
            )
            return
        try:
            stats = log_database_stats(self.codex_home)
        except Exception as exc:
            self._error("无法读取日志数据库", exc)
            return
        if not stats:
            messagebox.showinfo("没有日志数据库", "未找到可以压缩的 logs_2.sqlite。")
            return
        current = sum(int(item["file_bytes"]) for item in stats)
        reclaimable = sum(int(item["reclaimable_bytes"]) for item in stats)
        if not messagebox.askyesno(
            "释放数据库空闲空间",
            f"当前日志数据库：{format_bytes(current)}\n"
            f"预计可释放：{format_bytes(reclaimable)}\n\n"
            "此操作不会删除有效日志，只压缩已经空闲的数据库页面。是否继续？",
        ):
            return
        self._run_async("正在压缩日志数据库…", lambda: compact_log_databases(self.codex_home), self._compact_done)

    def scan_legacy_orphans(self) -> None:
        if self.application.id != "codex":
            return
        def done(result) -> None:
            if not result.candidates and not result.operation_log_ids and not result.state_reference_ids:
                messagebox.showinfo("历史残留扫描", "没有发现达到安全清理条件的历史残留。")
                return
            OrphanResultsWindow(self, result, self._execute_orphan_cleanup)

        self._run_async("正在只读扫描历史残留…", lambda: scan_orphans(self.codex_home), done)

    def _execute_orphan_cleanup(self, result) -> None:
        self._run_async(
            "正在暂存并删除历史残留（不复制附件）…",
            lambda: execute_orphan_cleanup(result),
            lambda report: self._orphan_cleanup_done(report),
        )

    def _orphan_cleanup_done(self, report) -> None:
        messagebox.showinfo(
            "历史残留清理完成",
            f"删除 {len(report.deleted_paths)} 项、{report.deleted_files} 个文件，"
            f"释放 {format_bytes(report.deleted_bytes)}；"
            f"操作日志脱敏 {report.operation_ids_redacted} 处；"
            f"全局状态引用清理 {report.state_references_removed} 处。",
        )
        self.refresh()

    def _compact_done(self, results) -> None:
        before = sum(int(item["before_bytes"]) for item in results)
        after = sum(int(item["after_bytes"]) for item in results)
        messagebox.showinfo(
            "数据库压缩完成",
            f"压缩前：{format_bytes(before)}\n压缩后：{format_bytes(after)}\n"
            f"释放空间：{format_bytes(max(before-after, 0))}\n完整性检查：通过",
        )

    def _delete_done(self, report) -> None:
        show_cleanup_result(self, report, self.application)
        self.current_id = None
        self.refresh()

    def _refresh_process_status(self) -> None:
        running = self.application.running(self.codex_home)
        label = self.application.label
        self.process_var.set(f"{label} 运行中：{', '.join(running)}" if running else f"{label} 已关闭：允许清理")
        self.after(3000, self._refresh_process_status)

    def open_transaction_recovery(self) -> None:
        home = Path(self.home_var.get()).expanduser()
        try:
            pending = pending_transactions(home)
        except OSError as exc:
            self._error("无法读取事务目录", exc)
            return
        if not pending:
            messagebox.showinfo("事务恢复", f"在 {home} 没有发现未完成事务。")
            return
        listing = "\n".join(path.name for path in pending[:8]) + ("\n…" if len(pending) > 8 else "")
        if len(pending) == 1:
            selected = pending[0]
        else:
            messagebox.showinfo(
                "发现未完成事务",
                f"发现 {len(pending)} 个中断的事务目录，请选择要恢复的一个：\n\n{listing}",
            )
            chosen = filedialog.askdirectory(initialdir=str(home), title="选择未完成的 .cleaner-transaction_ 或 .cleaner-restore- 目录")
            if not chosen:
                return
            selected = Path(chosen)
        if simpledialog.askstring(
            "恢复未完成事务",
            f"将按 {selected.name} 中记录的基线恢复文件，当前文件与该基线不同时会停止。\n\n输入 RECOVER 继续：",
            parent=self,
        ) != "RECOVER":
            return

        def work() -> int:
            return recover_transaction(selected, home, force=True)

        def done(count: int) -> None:
            messagebox.showinfo("恢复完成", f"已恢复 {count} 个文件。")
            self.refresh()

        self._run_async("正在恢复未完成事务…", work, done)

    def open_backup_manager(self) -> None:
        window = tk.Toplevel(self)
        window.title("备份管理")
        window.geometry("980x560")
        window.transient(self)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        root = self._backup_root().resolve()
        ttk.Label(frame, text=f"备份根目录：{root}", wraplength=930).pack(anchor="w", pady=(0, 8))
        columns = ("type", "status", "date", "sessions", "files", "size")
        tree = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="extended")
        tree.heading("#0", text="备份目录")
        tree.heading("type", text="类型")
        tree.heading("status", text="状态")
        tree.heading("date", text="创建时间")
        tree.heading("sessions", text="会话")
        tree.heading("files", text="文件")
        tree.heading("size", text="大小")
        tree.column("#0", width=280)
        tree.column("type", width=130)
        tree.column("status", width=100)
        tree.column("date", width=160)
        tree.column("sessions", width=60, anchor="e")
        tree.column("files", width=60, anchor="e")
        tree.column("size", width=90, anchor="e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        controls = ttk.Frame(frame, padding=(10, 0, 0, 0))
        controls.pack(side="right", fill="y")
        records = {}

        def selected_records():
            return [records[item] for item in tree.selection() if item in records]

        def selected_record():
            items = selected_records()
            return items[0] if len(items) == 1 else None

        def populate(items) -> None:
            tree.delete(*tree.get_children())
            records.clear()
            for index, item in enumerate(items):
                iid = f"backup:{index}"
                records[iid] = item
                tree.insert("", "end", iid=iid, text=item.name, values=(
                    item.backup_type, item.status, item.created_at[:19].replace("T", " "),
                    item.session_count, item.file_count, format_bytes(item.total_bytes),
                ))
            if not items:
                self.summary_var.set(f"未在 {root} 找到受管理的备份。")
            else:
                self.summary_var.set(f"备份 {len(items)} 个，共 {format_bytes(sum(item.total_bytes for item in items))}。")

        def refresh_backups() -> None:
            self._run_async("正在校验备份…", lambda: list_backups(root), populate)

        def verify_selected() -> None:
            record = selected_record()
            if not record:
                return
            self._run_async(
                "正在校验备份…", lambda: verify_backup(record.path),
                lambda manifest: messagebox.showinfo(
                    "校验通过", f"类型：{manifest.get('backup_type')}\n文件：{len(manifest.get('files', []))}\n所有哈希和 SQLite 检查通过。",
                    parent=window,
                ),
            )

        def remove_selected() -> None:
            if self.application.id != "codex":
                messagebox.showinfo("暂不支持", "DSH 备份管理目前支持浏览与校验。", parent=window)
                return
            selected = selected_records()
            if not selected:
                return
            if not messagebox.askyesno(
                "删除备份",
                f"将永久删除 {len(selected)} 个受管理备份，共 {format_bytes(sum(item.total_bytes for item in selected))}。\n\n"
                + "\n".join(item.name for item in selected[:8])
                + ("\n…" if len(selected) > 8 else "")
                + "\n\n不会删除备份根目录。是否继续？",
                parent=window,
            ):
                return
            def work():
                return sum(delete_backup(root, item.path, self.codex_home) for item in selected)
            self._run_async(
                "正在安全删除备份…", work,
                lambda released: (messagebox.showinfo("删除完成", f"已释放 {format_bytes(released)}。", parent=window), refresh_backups()),
            )

        def clean_by_policy() -> None:
            items = list(records.values())
            candidates = retention_candidates(
                items,
                keep_count=self.settings.backup_retention_count,
                max_age_days=self.settings.backup_retention_days,
                max_bytes=self.settings.backup_retention_mib * 1024 * 1024,
            )
            if not candidates:
                messagebox.showinfo("无需清理", "当前没有超出保留规则的备份，或尚未配置保留规则。", parent=window)
                return
            tree.selection_set([iid for iid, item in records.items() if item in candidates])
            remove_selected()

        def open_root() -> None:
            root.mkdir(parents=True, exist_ok=True)
            open_directory(root)

        def restore_selected() -> None:
            if self.application.id != "codex":
                messagebox.showinfo("导出副本", "DSH 导出包只读，不用于覆盖应用状态。", parent=window)
                return
            record = selected_record()
            if not record:
                return
            if not record.restorable:
                messagebox.showinfo("不可整体恢复", "会话归档包只用于保存和导出，不会覆盖当前 Codex 状态。", parent=window)
                return
            value = simpledialog.askstring(
                "恢复完整状态快照",
                "此操作会覆盖快照包含的完整状态数据库。\n恢复前会创建临时安全快照，失败时自动回到当前状态。\n\n输入 RESTORE 继续：",
                parent=window,
            )
            if value != "RESTORE":
                return
            self._run_async(
                "正在恢复完整状态快照…", lambda: restore_backup(record.path, self.codex_home, force=True),
                lambda count: (messagebox.showinfo("恢复完成", f"已恢复并校验 {count} 个文件。", parent=window), self.refresh()),
            )

        ttk.Button(controls, text="重新扫描", command=refresh_backups).pack(fill="x")
        ttk.Button(controls, text="校验所选", command=verify_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="恢复所选", command=restore_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="删除所选", style="Danger.TButton", command=remove_selected).pack(fill="x", pady=(20, 0))
        ttk.Button(controls, text="按保留规则清理", command=clean_by_policy).pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="打开备份目录", command=open_root).pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="关闭", command=window.destroy).pack(fill="x", pady=(20, 0))
        refresh_backups()

    def open_settings(self) -> None:
        window = tk.Toplevel(self)
        window.title("设置")
        window.transient(self)
        window.grab_set()
        window.resizable(False, False)
        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        general_tab = ttk.Frame(notebook, padding=14)
        codex_tab = ttk.Frame(notebook, padding=14)
        dsh_tab = ttk.Frame(notebook, padding=14)
        notebook.add(general_tab, text="常规")
        notebook.add(codex_tab, text="Codex")
        notebook.add(dsh_tab, text="DSH")

        backup_var = tk.BooleanVar(value=self.settings.backup_before_delete)
        internal_var = tk.BooleanVar(value=self.settings.show_internal_messages)
        backup_dir_var = tk.StringVar(value=self.settings.backup_directory)
        confirmation_var = tk.StringVar(value=self.settings.confirmation_text)
        retention_count_var = tk.IntVar(value=self.settings.backup_retention_count)
        retention_days_var = tk.IntVar(value=self.settings.backup_retention_days)
        retention_mib_var = tk.IntVar(value=self.settings.backup_retention_mib)

        ttk.Checkbutton(general_tab, text="删除前保留持久备份（默认关闭）", variable=backup_var).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(general_tab, text="显示系统/开发者及注入消息", variable=internal_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 12))
        ttk.Label(general_tab, text="默认备份目录:").grid(row=2, column=0, sticky="w")
        ttk.Entry(general_tab, textvariable=backup_dir_var, width=58).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        ttk.Button(general_tab, text="选择", command=lambda: self._choose_setting_dir(backup_dir_var)).grid(row=3, column=2, padx=(6, 0), pady=(4, 10))
        ttk.Label(general_tab, text="删除确认文字（可用 {count} 表示删除数量）:").grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Entry(general_tab, textvariable=confirmation_var, width=58).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 14))
        ttk.Label(general_tab, text="备份保留规则（0 表示不限制）:").grid(row=6, column=0, columnspan=3, sticky="w")
        retention = ttk.Frame(general_tab)
        retention.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Label(retention, text="数量").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=9999, width=7, textvariable=retention_count_var).pack(side="left", padx=(4, 14))
        ttk.Label(retention, text="天数").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=9999, width=7, textvariable=retention_days_var).pack(side="left", padx=(4, 14))
        ttk.Label(retention, text="最大 MiB").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=1048576, width=10, textvariable=retention_mib_var).pack(side="left", padx=(4, 0))
        general_tab.columnconfigure(0, weight=1)

        binary_var = tk.StringVar(value=self.settings.codex_binary)
        ttk.Label(codex_tab, text="Codex 可执行文件（留空自动检测）:").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(codex_tab, textvariable=binary_var, width=58).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(codex_tab, text="选择", command=lambda: self._choose_binary(binary_var)).grid(row=1, column=2, padx=(6, 0), pady=(4, 0))
        ttk.Label(codex_tab, text="这些设置只在应用切换到 Codex 时使用。").grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        codex_tab.columnconfigure(0, weight=1)

        dsh_home_var = tk.StringVar(value=self.settings.dsh_home)
        dsh_root_var = tk.StringVar(value=self.settings.dsh_session_root)
        ttk.Label(dsh_tab, text="DSH 数据目录（留空使用默认位置）:").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(dsh_tab, textvariable=dsh_home_var, width=58).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        ttk.Button(dsh_tab, text="选择", command=lambda: self._choose_setting_dir(dsh_home_var)).grid(row=1, column=2, padx=(6, 0), pady=(4, 10))
        ttk.Label(dsh_tab, text="DSH 会话根目录（留空使用数据目录下的 sessions）:").grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Entry(dsh_tab, textvariable=dsh_root_var, width=58).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(dsh_tab, text="选择", command=lambda: self._choose_setting_dir(dsh_root_var)).grid(row=3, column=2, padx=(6, 0), pady=(4, 0))
        dsh_tab.columnconfigure(0, weight=1)

        def save() -> None:
            self.settings = AppSettings(
                backup_before_delete=backup_var.get(),
                backup_directory=backup_dir_var.get().strip(),
                include_state_in_backups=self.settings.include_state_in_backups,
                show_internal_messages=internal_var.get(),
                language=self.settings.language,
                theme=self.settings.theme,
                codex_binary=binary_var.get().strip(),
                confirmation_text=confirmation_var.get().strip() or "DELETE",
                backup_retention_count=max(0, retention_count_var.get()),
                backup_retention_days=max(0, retention_days_var.get()),
                backup_retention_mib=max(0, retention_mib_var.get()),
                dsh_home=dsh_home_var.get().strip(),
                dsh_session_root=dsh_root_var.get().strip(),
            )
            save_settings(self.settings)
            if self.application.id == "dsh":
                if self.settings.dsh_home:
                    self.home_var.set(self.settings.dsh_home)
                if self.settings.dsh_session_root:
                    self.session_root_var.set(self.settings.dsh_session_root)
            window.destroy()
            if self.current_id:
                self._on_select()

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=window.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=save).pack(side="right", padx=6)

    @staticmethod
    def _choose_setting_dir(variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory(initialdir=variable.get() or None, title="选择默认备份目录")
        if selected:
            variable.set(selected)

    @staticmethod
    def _choose_binary(variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(initialdir=str(Path(variable.get()).parent) if variable.get() else None, title="选择 Codex 可执行文件")
        if selected:
            variable.set(selected)

    def _error(self, title: str, exc: Exception) -> None:
        self._busy(False)
        self.summary_var.set(str(exc))
        messagebox.showerror(title, str(exc))


def launch(codex_home: Path | None = None, application: str = "codex", session_root: Path | None = None) -> None:
    get_platform().prepare_gui_runtime()
    CleanerApp(codex_home or get_application(application).default_home(), application, session_root).mainloop()


def main() -> None:
    launch()


if __name__ == "__main__":
    main()
