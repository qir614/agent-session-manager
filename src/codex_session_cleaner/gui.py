from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from .backup import create_manual_backup, verify_backup
from .backup_manager import delete_backup, list_backups, retention_candidates
from .cli import format_bytes
from .codex_controller import CodexController
from .conversation_reader import ConversationDetails, read_conversation
from .executor import CleanupExecutor, restore_backup
from .impact_analyzer import analyze_deletion
from .models import SessionStatus
from .maintenance import compact_log_databases, log_database_stats
from .planner import build_plan
from .process_guard import running_codex_processes
from .projects import ProjectInfo, project_info
from .scanner import SessionScanner, default_codex_home
from .settings import AppSettings, load_settings, save_settings


STATUS_LABELS = {
    SessionStatus.USER_ROOT: "用户对话",
    SessionStatus.ATTACHED: "附属子智能体",
    SessionStatus.ORPHAN: "孤儿子智能体",
    SessionStatus.UNLINKED_INTERNAL: "无父内部线程",
    SessionStatus.LOCAL_GHOST: "本地幽灵记录",
    SessionStatus.CLOUD_CATALOG: "云端目录（只读）",
    SessionStatus.UNKNOWN: "未知",
}


class CleanerApp(tk.Tk):
    def __init__(self, codex_home: Path):
        super().__init__()
        self.title(f"Codex 本地会话管理器 v{__version__}")
        self.geometry("1420x860")
        self.minsize(1060, 680)
        self.codex_home = codex_home.expanduser()
        self.settings = load_settings()
        self.scan_result = None
        self.current_id: str | None = None
        self._metadata: dict[str, object] = {}
        self.project_label_to_key: dict[str, str | None] = {"全部项目": None}
        self.project_node_ids: dict[str, set[str]] = {}
        self.group_node_ids: dict[str, set[str]] = {}
        self._build_style()
        self._build_ui()
        self.after(100, self.refresh)
        self.after(1000, self._refresh_process_status)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=27)
        style.configure("Danger.TButton", foreground="#9f1d1d")
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(12, 12, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Codex 目录:").pack(side="left")
        self.home_var = tk.StringVar(value=str(self.codex_home))
        ttk.Entry(top, textvariable=self.home_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="选择", command=self.choose_home).pack(side="left")
        ttk.Button(top, text="重新扫描", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="设置", command=self.open_settings).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="备份管理", command=self.open_backup_manager).pack(side="left", padx=(8, 0))

        filters = ttk.Frame(self, padding=(12, 4, 12, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="搜索:").pack(side="left")
        self.query_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.query_var, width=28)
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
            width=22,
            values=("全部项目",),
        )
        self.project_combo.pack(side="left", padx=6)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_tree())
        ttk.Button(filters, text="归档全部", command=lambda: self.manual_backup(all_sessions=True)).pack(side="right")
        ttk.Button(filters, text="释放数据库空闲空间", command=self.compact_logs).pack(side="right", padx=(0, 8))
        self.process_var = tk.StringVar(value="正在检查 Codex 状态…")
        ttk.Label(filters, textvariable=self.process_var).pack(side="right", padx=14)

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane, padding=(12, 2, 0, 0))
        pane.add(left, weight=3)
        pane.add(right, weight=4)

        columns = ("project", "status", "date", "size", "children")
        selection_bar = ttk.Frame(left)
        selection_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(selection_bar, text="选择全部结果", command=self.select_all_visible).pack(side="left")
        ttk.Button(selection_bar, text="反选", command=self.invert_visible_selection).pack(side="left", padx=6)
        ttk.Button(selection_bar, text="清除选择", command=lambda: self.tree.selection_remove(self.tree.selection())).pack(side="left")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", selectmode="extended")
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
        ttk.Label(header, textvariable=self.title_var, style="Title.TLabel", wraplength=620).pack(anchor="w")
        self.facts_var = tk.StringVar(value="扫描后可查看消息、依赖关系与元数据。")
        ttk.Label(header, textvariable=self.facts_var, foreground="#555", wraplength=720).pack(anchor="w", pady=(5, 8))

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(0, 8))
        self.rename_button = ttk.Button(actions, text="重命名", command=self.rename_selected, state="disabled")
        self.rename_button.pack(side="left")
        self.archive_button = ttk.Button(actions, text="归档", command=self.toggle_archive, state="disabled")
        self.archive_button.pack(side="left", padx=6)
        ttk.Button(actions, text="归档所选", command=self.manual_backup).pack(side="left")
        ttk.Button(actions, text="删除所选", style="Danger.TButton", command=self.delete_selected).pack(side="right")

        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True)
        messages_tab = ttk.Frame(tabs)
        metadata_tab = ttk.Frame(tabs)
        tabs.add(messages_tab, text="对话内容")
        tabs.add(metadata_tab, text="元数据与依赖")
        self.messages_text = tk.Text(messages_tab, wrap="word", state="disabled", padx=12, pady=10)
        msg_scroll = ttk.Scrollbar(messages_tab, orient="vertical", command=self.messages_text.yview)
        self.messages_text.configure(yscrollcommand=msg_scroll.set)
        self.messages_text.pack(side="left", fill="both", expand=True)
        msg_scroll.pack(side="right", fill="y")
        self.messages_text.tag_configure("role", font=("Segoe UI", 10, "bold"), foreground="#245a9b")
        self.messages_text.tag_configure("meta", foreground="#777")
        self.metadata_text = tk.Text(metadata_tab, wrap="none", state="disabled", padx=12, pady=10)
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

    def choose_home(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.home_var.get(), title="选择 .codex 目录")
        if selected:
            self.home_var.set(selected)
            self.refresh()

    def _busy(self, value: bool, message: str = "") -> None:
        self.progress.start(10) if value else self.progress.stop()
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
        self._run_async("正在只读扫描…", lambda: SessionScanner(home).scan(), self._render)

    def _render(self, scan) -> None:
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
            messagebox.showwarning("发现阻止性问题", "存在损坏、重复或循环依赖记录；删除功能将拒绝执行。")

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
                    text=f"📁 {info.name} ({len(session_ids)})",
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
                record.updated_at[:19].replace("T", " "),
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
        editable = status == SessionStatus.USER_ROOT and record.has_rollout
        self.rename_button.configure(state="normal" if editable else "disabled")
        self.archive_button.configure(state="normal" if editable else "disabled", text="取消归档" if record.archived else "归档")
        metadata = {
            "id": record.id,
            "status": status.value,
            "kind": record.kind.value,
            "title": record.title,
            "archived": record.archived,
            "parent_id": parent,
            "child_ids": children,
            "cwd": record.cwd,
            "project": {"name": project.name, "path": project.path, "temporary": project.temporary},
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "path": str(record.path) if record.path else None,
            "size": record.size,
            "warnings": record.warnings,
        }
        self._metadata = metadata
        self._set_text(self.metadata_text, json.dumps(metadata, ensure_ascii=False, indent=2))
        if record.path is None:
            note = (
                "这是云端目录记录。本地工具只能查看目录信息，不能删除服务器上的对话。"
                if status == SessionStatus.CLOUD_CATALOG else
                "这是没有 rollout 文件的本地幽灵记录，可在 Codex 关闭后清理其侧边栏索引。"
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
            lambda: read_conversation(record.path, include_internal=self.settings.show_internal_messages),
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
            heading = f"{message.role.upper()}  {message.timestamp}\n"
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
        if not self.current_id or not self.scan_result:
            return
        record = self.scan_result.sessions[self.current_id]
        value = simpledialog.askstring("重命名会话", "新标题（1-200 字符）:", initialvalue=record.title, parent=self)
        if value is None:
            return
        controller = CodexController(self.codex_home, self.settings.codex_binary)
        self._run_async("正在通过 Codex 重命名…", lambda: controller.rename(record.id, value), lambda _result: self.refresh())

    def toggle_archive(self) -> None:
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
            "正在创建会话归档包…",
            lambda: create_manual_backup(
                self.scan_result,
                selected_ids=ids,
                destination=destination,
                include_state=False,
            ),
            lambda result: messagebox.showinfo(
                "归档完成",
                f"会话归档包已创建：\n{result[0]}\n\n"
                "归档包用于保存和导出，不会覆盖当前 Codex 状态。",
            ),
        )

    def delete_selected(self) -> None:
        if not self.scan_result:
            return
        selected = self._selected_ids()
        if not selected:
            messagebox.showinfo("没有选择", "请先选择要删除的会话。")
            return
        running = running_codex_processes()
        if running:
            messagebox.showwarning(
                "请先完全关闭 Codex",
                "查看、搜索、重命名和手动备份可以在 Codex 运行时执行。\n\n"
                "删除会同时更新会话文件和本地索引，因此只有完全关闭 Codex 后才能继续。\n"
                f"检测到：{', '.join(running)}",
            )
            return
        try:
            plan = build_plan(self.scan_result, selected)
            impact = analyze_deletion(self.scan_result, plan)
        except Exception as exc:
            self._error("无法生成删除计划", exc)
            return
        backup_enabled = self.settings.backup_before_delete
        warning = (
            "设置已开启：删除前会保留持久备份。"
            if backup_enabled
            else "设置中未开启删除前备份：本次删除不可恢复。"
        )
        configured = self.settings.confirmation_text.strip() or "DELETE"
        phrase = configured.replace("{count}", str(len(plan.deletion_ids)))
        value = simpledialog.askstring(
            "删除影响预览与最终确认",
            f"主对话：{impact.user_count}\n"
            f"子智能体：{impact.subagent_count}\n"
            f"本地幽灵：{impact.ghost_count}\n"
            f"rollout：{impact.rollout_count} 个，{format_bytes(impact.rollout_bytes)}\n"
            f"数据库引用：{impact.sqlite_reference_total} 行\n"
            f"其中日志：{impact.log_reference_total} 行\n"
            f"事务回滚快照预计：{format_bytes(impact.transaction_snapshot_bytes)}\n\n"
            "不会删除附件、生成图片、可视化或项目文件。\n"
            f"{warning}\n\n输入 {phrase} 继续：",
            parent=self,
        )
        if value != phrase:
            return
        backup_dir = self._backup_root()
        self._run_async(
            "正在删除并更新索引…",
            lambda: CleanupExecutor().execute(plan, persistent_backup=backup_enabled, backup_directory=backup_dir),
            self._delete_done,
        )

    def compact_logs(self) -> None:
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

    def _compact_done(self, results) -> None:
        before = sum(int(item["before_bytes"]) for item in results)
        after = sum(int(item["after_bytes"]) for item in results)
        messagebox.showinfo(
            "数据库压缩完成",
            f"压缩前：{format_bytes(before)}\n压缩后：{format_bytes(after)}\n"
            f"释放空间：{format_bytes(max(before-after, 0))}\n完整性检查：通过",
        )

    def _delete_done(self, report) -> None:
        backup = str(report.backup_dir) if report.backup_dir else "未创建（按当前设置）"
        rows = sum(sum(value.values()) for value in report.sqlite_rows_removed_all.values())
        log_rows = sum(
            sum(report.sqlite_rows_removed_all.get(name, {}).values())
            for name in ("logs_2.sqlite", "sqlite/logs_2.sqlite")
        )
        messagebox.showinfo(
            "删除完成",
            f"会话及子智能体：{len(report.deleted_ids)}\n"
            f"rollout 文件：{len(report.deleted_files)}\n"
            f"数据库记录：{rows} 行\n"
            f"其中日志：{log_rows} 行\n"
            f"逐库存留检查：通过\n事务回滚验证：通过\n"
            f"持久完整快照：{backup}",
        )
        self.current_id = None
        self.refresh()

    def _refresh_process_status(self) -> None:
        running = running_codex_processes()
        self.process_var.set(f"Codex 运行中：{', '.join(running)}" if running else "Codex 已关闭：允许删除")
        self.after(3000, self._refresh_process_status)

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
            if os.name == "nt":
                os.startfile(root)  # type: ignore[attr-defined]

        def restore_selected() -> None:
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
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        backup_var = tk.BooleanVar(value=self.settings.backup_before_delete)
        internal_var = tk.BooleanVar(value=self.settings.show_internal_messages)
        backup_dir_var = tk.StringVar(value=self.settings.backup_directory)
        binary_var = tk.StringVar(value=self.settings.codex_binary)
        confirmation_var = tk.StringVar(value=self.settings.confirmation_text)
        retention_count_var = tk.IntVar(value=self.settings.backup_retention_count)
        retention_days_var = tk.IntVar(value=self.settings.backup_retention_days)
        retention_mib_var = tk.IntVar(value=self.settings.backup_retention_mib)
        ttk.Checkbutton(frame, text="删除前保留持久备份（默认关闭）", variable=backup_var).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="显示系统/开发者及注入消息", variable=internal_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 12))
        ttk.Label(frame, text="默认备份目录:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=backup_dir_var, width=58).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        ttk.Button(frame, text="选择", command=lambda: self._choose_setting_dir(backup_dir_var)).grid(row=3, column=2, padx=(6, 0), pady=(4, 10))
        ttk.Label(frame, text="Codex 可执行文件（留空自动检测）:").grid(row=4, column=0, sticky="w")
        ttk.Entry(frame, textvariable=binary_var, width=58).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 14))
        ttk.Button(frame, text="选择", command=lambda: self._choose_binary(binary_var)).grid(row=5, column=2, padx=(6, 0), pady=(4, 14))
        ttk.Label(frame, text="删除确认文字（可用 {count} 表示删除数量）:").grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Entry(frame, textvariable=confirmation_var, width=58).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 14))
        ttk.Label(frame, text="备份保留规则（0 表示不限制）:").grid(row=8, column=0, columnspan=3, sticky="w")
        retention = ttk.Frame(frame)
        retention.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(4, 14))
        ttk.Label(retention, text="数量").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=9999, width=7, textvariable=retention_count_var).pack(side="left", padx=(4, 14))
        ttk.Label(retention, text="天数").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=9999, width=7, textvariable=retention_days_var).pack(side="left", padx=(4, 14))
        ttk.Label(retention, text="最大 MiB").pack(side="left")
        ttk.Spinbox(retention, from_=0, to=1048576, width=10, textvariable=retention_mib_var).pack(side="left", padx=(4, 0))

        def save() -> None:
            self.settings = AppSettings(
                backup_before_delete=backup_var.get(),
                backup_directory=backup_dir_var.get().strip(),
                include_state_in_backups=False,
                show_internal_messages=internal_var.get(),
                language=self.settings.language,
                theme=self.settings.theme,
                codex_binary=binary_var.get().strip(),
                confirmation_text=confirmation_var.get().strip() or "DELETE",
                backup_retention_count=max(0, retention_count_var.get()),
                backup_retention_days=max(0, retention_days_var.get()),
                backup_retention_mib=max(0, retention_mib_var.get()),
            )
            save_settings(self.settings)
            window.destroy()
            if self.current_id:
                self._on_select()

        buttons = ttk.Frame(frame)
        buttons.grid(row=10, column=0, columnspan=3, sticky="e")
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


def launch(codex_home: Path | None = None) -> None:
    CleanerApp(codex_home or default_codex_home()).mainloop()


def main() -> None:
    launch()


if __name__ == "__main__":
    main()
