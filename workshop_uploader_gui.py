from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import traceback
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import app_settings
import asset_upload
from app_settings import AssetSourceMode
from asset_sync import AssetRepository, SyncState
from git_client import GitRunner, ensure_askpass
from github_auth import DeviceCode, GitHubApiClient, GitHubAuthManager, GitHubSession
from github_config import GitHubConfigError, load_github_app_config
from gui_state import compute_action_availability
from localization import normalize_language, set_language, tr
from pending_upload import PendingPhase, PendingUploadError, PendingUploadStore
from repository_target import (
    DEFAULT_ASSET_SUBDIR,
    DEFAULT_BRANCH,
    DEFAULT_REPOSITORY,
    RepositoryTarget,
)
from update_notes import UPDATE_NOTE_PLACEHOLDER
from upload_workflow import UploadOptions, UploadWorkflow, WorkflowResult
from windows_credentials import WindowsCredentialStore


APP_TITLE = "CS2 Workshop Uploader"
DEFAULT_WORKSHOP_ID = ""
SETTINGS_PATH = asset_upload.RUNTIME_PATH / "settings.json"
PENDING_PATH = asset_upload.RUNTIME_PATH / "pending_upload.json"

BG = "#F3F5F8"
CARD = "#FFFFFF"
NAVY = "#15243A"
MUTED = "#64748B"
TEXT = "#172033"
BORDER = "#D8E0EA"
ACCENT = "#1473E6"
ACCENT_ACTIVE = "#0E5FC2"
SUCCESS = "#14804A"
DANGER = "#B42318"
FONT_FAMILY = "Malgun Gothic"
FONT_TITLE = ("Segoe UI", 22)
FONT_SECTION = (FONT_FAMILY, 12)
FONT_BODY = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, str]:
    return app_settings.load_settings(path)


def save_settings(settings: dict[str, str], path: Path = SETTINGS_PATH) -> None:
    app_settings.save_settings(path, settings)


class QueueWriter:
    def __init__(self, events: queue.Queue):
        self.events = events

    def write(self, value: str) -> int:
        if value:
            self.events.put(("log", value.replace("\r", "\n")))
        return len(value)

    def flush(self) -> None:
        return None


class WorkshopUploaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.last_pack_folder: Path | None = None
        self.session: GitHubSession | None = None
        self.auth_manager: GitHubAuthManager | None = None
        self.repository_factory = None
        self.sync_state = SyncState.LOGIN_REQUIRED
        self.cancel_login = threading.Event()
        self.pending_store = PendingUploadStore(PENDING_PATH)
        self.workflow = UploadWorkflow(asset_upload.auto_update, None, self.pending_store)

        saved = load_settings()
        self.language = tk.StringVar(
            value=normalize_language(saved.get("language"))
        )
        set_language(self.language.get())
        saved_mode = app_settings.selected_mode(saved)
        github_repository = saved.get("github_repository", DEFAULT_REPOSITORY)
        github_branch = saved.get("github_branch", DEFAULT_BRANCH)
        github_asset_subdir = saved.get("github_asset_subdir", DEFAULT_ASSET_SUBDIR)
        if self.pending_store.exists():
            saved_mode = AssetSourceMode.GITHUB
            try:
                pending = self.pending_store.load()
                if pending is not None:
                    github_repository = pending.target.full_name
                    github_branch = pending.target.branch
                    github_asset_subdir = pending.target.asset_subdir_text
            except PendingUploadError:
                pass
        self.asset_source_mode = tk.StringVar(
            value=saved_mode.value if saved_mode is not None else ""
        )
        self._previous_mode = self.asset_source_mode.get()
        self.workshop_id = tk.StringVar(
            value=saved.get("workshop_id", DEFAULT_WORKSHOP_ID)
        )
        self.workshop_title = tk.StringVar(value=saved.get("workshop_title", ""))
        self.workshop_description = tk.StringVar(
            value=saved.get("workshop_description", "")
        )
        self.chunk_size = tk.StringVar(
            value=saved.get("chunk_size_mb", str(asset_upload.DEFAULT_CHUNK_SIZE_MB))
        )
        self.asset_folder = tk.StringVar(value=saved.get("asset_folder", ""))
        self.github_repository = tk.StringVar(value=github_repository)
        self.github_branch = tk.StringVar(value=github_branch)
        self.github_asset_subdir = tk.StringVar(value=github_asset_subdir)
        self.github_asset_folder = tk.StringVar(value="")
        self.output_folder = tk.StringVar(
            value=saved.get(
                "output_folder", str(asset_upload.RUNTIME_PATH / "output")
            )
        )
        self.preview_path = tk.StringVar(value=saved.get("preview_path", ""))
        self.status = tk.StringVar(value=tr("준비됨", "Ready"))
        self.github_status = tk.StringVar(
            value=tr("GitHub 로그인 필요", "GitHub login required")
        )
        self.github_progress = tk.DoubleVar(value=0)
        self.github_progress_text = tk.StringVar(value="")
        self.note_placeholder_active = False
        self._refresh_github_asset_folder()

        self._configure_window()
        self._build_ui()
        self.asset_folder.trace_add("write", lambda *_args: self._apply_availability())
        for variable in (
            self.github_repository,
            self.github_branch,
            self.github_asset_subdir,
        ):
            variable.trace_add("write", lambda *_args: self._on_github_target_change())
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if saved_mode is None and not self.pending_store.exists():
            self.root.after(0, self._prompt_initial_mode)
        else:
            self.root.after(0, self._apply_mode)

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("940x1020")
        self.root.minsize(850, 940)
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Uploader.TEntry",
            fieldbackground=CARD,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=8,
        )
        style.configure(
            "Uploader.TSpinbox",
            fieldbackground=CARD,
            foreground=TEXT,
            bordercolor=BORDER,
            arrowcolor=TEXT,
            padding=7,
        )

    def _collect_settings(self) -> dict[str, str]:
        values = {
            "language": self.language.get(),
            "workshop_id": self.workshop_id.get(),
            "workshop_title": self.workshop_title.get(),
            "workshop_description": self.workshop_description.get(),
            "chunk_size_mb": self.chunk_size.get(),
            "asset_folder": self.asset_folder.get(),
            "output_folder": self.output_folder.get(),
            "preview_path": self.preview_path.get(),
            "github_repository": self.github_repository.get(),
            "github_branch": self.github_branch.get(),
            "github_asset_subdir": self.github_asset_subdir.get(),
        }
        mode = getattr(self, "asset_source_mode", None)
        if mode is not None and mode.get() in {
            AssetSourceMode.LOCAL.value,
            AssetSourceMode.GITHUB.value,
        }:
            values["asset_source_mode"] = mode.get()
        return values

    def _save_settings(self) -> bool:
        try:
            save_settings(self._collect_settings())
        except OSError as error:
            messagebox.showwarning(
                APP_TITLE,
                tr("설정을 저장하지 못했습니다.\n\n{error}", "Could not save settings.\n\n{error}", error=error),
                parent=self.root,
            )
            return False
        return True

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )

    def _section_title(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=FONT_SECTION)

    def _button(self, parent, text, command, *, secondary: bool) -> tk.Button:
        if secondary:
            background, foreground, active, border = CARD, TEXT, "#E7EDF5", BORDER
        else:
            background, foreground, active, border = (
                ACCENT,
                "white",
                ACCENT_ACTIVE,
                ACCENT,
            )
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=active,
            activeforeground=foreground,
            disabledforeground="#A8B2BF",
            relief="flat",
            highlightbackground=border,
            highlightthickness=1,
            bd=0,
            padx=16,
            pady=9,
            cursor="hand2",
            font=FONT_BODY,
        )

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=104)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.grid_columnconfigure(0, weight=1)
        title_panel = tk.Frame(header, bg=NAVY)
        title_panel.grid(row=0, column=0, sticky="w", padx=28, pady=(16, 0))
        tk.Label(
            title_panel,
            text="CS2 Workshop Uploader",
            bg=NAVY,
            fg="white",
            font=FONT_TITLE,
        ).pack(anchor="w")
        tk.Label(
            title_panel,
            text=tr(
                "로컬 폴더 또는 GitHub 동기화 · VPK v2 · Steam Workshop",
                "Local folder or GitHub sync · VPK v2 · Steam Workshop",
            ),
            bg=NAVY,
            fg="#D7E4F5",
            font=FONT_BODY,
        ).pack(anchor="w", pady=(2, 0))
        language_panel = tk.Frame(header, bg=NAVY)
        language_panel.grid(row=0, column=1, sticky="e", padx=24, pady=(20, 0))
        self.language_buttons = []
        for label, value in (("한국어", "ko"), ("English", "en")):
            button = tk.Radiobutton(
                language_panel,
                text=label,
                variable=self.language,
                value=value,
                command=self._on_language_change,
                indicatoron=False,
                bg="#233754",
                fg="#D7E4F5",
                selectcolor=ACCENT,
                activebackground=ACCENT,
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=10,
                pady=5,
                font=FONT_SMALL,
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 4))
            self.language_buttons.append(button)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)
        settings = self._card(body)
        settings.grid(row=0, column=0, sticky="ew")
        settings.grid_columnconfigure(1, weight=1)
        self._section_title(settings, tr("업로드 설정", "Upload settings")).grid(
            row=0, column=0, columnspan=5, sticky="w", padx=18, pady=(16, 12)
        )

        self._label(settings, tr("에셋 관리", "Asset source"), 1)
        modes = tk.Frame(settings, bg=CARD)
        modes.grid(row=1, column=1, columnspan=4, sticky="w", pady=(0, 12))
        self.local_mode_button = tk.Radiobutton(
            modes,
            text=tr("로컬 폴더", "Local folder"),
            variable=self.asset_source_mode,
            value=AssetSourceMode.LOCAL.value,
            command=self._on_mode_change,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            font=FONT_BODY,
        )
        self.local_mode_button.pack(side="left", padx=(0, 18))
        self.github_mode_button = tk.Radiobutton(
            modes,
            text=tr("GitHub 동기화", "GitHub sync"),
            variable=self.asset_source_mode,
            value=AssetSourceMode.GITHUB.value,
            command=self._on_mode_change,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            font=FONT_BODY,
        )
        self.github_mode_button.pack(side="left")

        self._label(settings, "Workshop Addon ID", 2)
        self.id_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_id,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.id_entry.grid(row=2, column=1, sticky="ew", padx=(0, 20), pady=(0, 12))
        tk.Label(
            settings,
            text=tr("청크 크기 (MiB)", "Chunk size (MiB)"),
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=2, column=2, sticky="w", padx=(0, 10), pady=(0, 12))
        self.chunk_entry = ttk.Spinbox(
            settings,
            from_=1,
            to=2048,
            textvariable=self.chunk_size,
            width=8,
            style="Uploader.TSpinbox",
            font=FONT_BODY,
        )
        self.chunk_entry.grid(row=2, column=3, sticky="e", padx=(0, 18), pady=(0, 12))

        self._label(settings, tr("Workshop 제목", "Workshop title"), 3)
        self.title_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_title,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.title_entry.grid(
            row=3, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )
        self._label(settings, tr("Workshop 설명", "Workshop description"), 4)
        self.description_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_description,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.description_entry.grid(
            row=4, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, tr("에셋 폴더", "Asset folder"), 5)
        self.asset_path_entry = ttk.Entry(
            settings,
            textvariable=self.asset_folder,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.asset_path_entry.grid(
            row=5, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 12)
        )
        self.asset_browse_button = self._button(
            settings, tr("찾아보기", "Browse"), self._choose_asset_folder, secondary=True
        )
        self.asset_browse_button.grid(
            row=5, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, tr("출력 폴더", "Output folder"), 6)
        self.output_path_entry = ttk.Entry(
            settings,
            textvariable=self.output_folder,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.output_path_entry.grid(
            row=6, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 12)
        )
        self.output_browse_button = self._button(
            settings, tr("찾아보기", "Browse"), self._choose_output_folder, secondary=True
        )
        self.output_browse_button.grid(
            row=6, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, tr("미리보기 이미지", "Preview image"), 7)
        self.preview_path_entry = ttk.Entry(
            settings,
            textvariable=self.preview_path,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.preview_path_entry.grid(
            row=7, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 12)
        )
        self.preview_browse_button = self._button(
            settings, tr("찾아보기", "Browse"), self._choose_preview_image, secondary=True
        )
        self.preview_browse_button.grid(
            row=7, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, tr("업데이트 내역", "Update notes"), 8, anchor="nw")
        self.update_note = tk.Text(
            settings,
            height=4,
            wrap="word",
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            padx=8,
            pady=7,
            font=FONT_BODY,
        )
        self.update_note.grid(
            row=8, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 14)
        )
        self.update_note.bind("<FocusIn>", self._on_note_focus_in)
        self.update_note.bind("<FocusOut>", self._on_note_focus_out)
        self._show_note_placeholder()

        self.github_card = self._card(body)
        self.github_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.github_card.grid_columnconfigure(0, weight=1)
        github_text = tk.Frame(self.github_card, bg=CARD)
        github_text.grid(row=0, column=0, sticky="ew", padx=18, pady=13)
        github_text.grid_columnconfigure(0, weight=1)
        tk.Label(
            github_text,
            text=tr("GitHub 에셋 동기화", "GitHub asset sync"),
            bg=CARD,
            fg=TEXT,
            font=FONT_SECTION,
        ).grid(row=0, column=0, sticky="w")
        self.github_status_label = tk.Label(
            github_text,
            textvariable=self.github_status,
            bg=CARD,
            fg=MUTED,
            font=FONT_SMALL,
            anchor="w",
            justify="left",
        )
        self.github_status_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.github_action_button = self._button(
            self.github_card, tr("GitHub 로그인", "GitHub login"), self._github_action, secondary=True
        )
        self.github_action_button.grid(row=0, column=1, padx=(8, 18), pady=13)

        github_target = tk.Frame(self.github_card, bg=CARD)
        github_target.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14)
        )
        github_target.grid_columnconfigure(1, weight=3)
        github_target.grid_columnconfigure(3, weight=1)
        github_target.grid_columnconfigure(5, weight=2)
        tk.Label(
            github_target,
            text=tr("저장소", "Repository"),
            bg=CARD,
            fg=TEXT,
            font=FONT_SMALL,
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.github_repository_entry = ttk.Entry(
            github_target,
            textvariable=self.github_repository,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.github_repository_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 12)
        )
        tk.Label(
            github_target,
            text=tr("브랜치", "Branch"),
            bg=CARD,
            fg=TEXT,
            font=FONT_SMALL,
        ).grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.github_branch_entry = ttk.Entry(
            github_target,
            textvariable=self.github_branch,
            style="Uploader.TEntry",
            font=FONT_BODY,
            width=12,
        )
        self.github_branch_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        tk.Label(
            github_target,
            text=tr("에셋 경로", "Asset path"),
            bg=CARD,
            fg=TEXT,
            font=FONT_SMALL,
        ).grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.github_asset_subdir_entry = ttk.Entry(
            github_target,
            textvariable=self.github_asset_subdir,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.github_asset_subdir_entry.grid(row=0, column=5, sticky="ew")

        self.github_progress_frame = tk.Frame(self.github_card, bg=CARD)
        self.github_progress_frame.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14)
        )
        self.github_progress_frame.grid_columnconfigure(0, weight=1)
        self.github_progress_bar = ttk.Progressbar(
            self.github_progress_frame,
            variable=self.github_progress,
            maximum=100,
            mode="determinate",
        )
        self.github_progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        tk.Label(
            self.github_progress_frame,
            textvariable=self.github_progress_text,
            bg=CARD,
            fg=TEXT,
            font=FONT_SMALL,
            width=30,
            anchor="e",
        ).grid(row=0, column=1, sticky="e")
        self.github_progress_frame.grid_remove()

        actions = tk.Frame(body, bg=BG)
        actions.grid(row=2, column=0, sticky="ew", pady=14)
        actions.grid_columnconfigure(0, weight=1)
        folders = tk.Frame(actions, bg=BG)
        folders.grid(row=0, column=0, sticky="w")
        self.asset_button = self._button(
            folders, tr("에셋 폴더 열기", "Open asset folder"), self._open_asset_folder, secondary=True
        )
        self.asset_button.pack(side="left", padx=(0, 8))
        self.output_button = self._button(
            folders, tr("출력 폴더 열기", "Open output folder"), self._open_output_folder, secondary=True
        )
        self.output_button.pack(side="left")
        runs = tk.Frame(actions, bg=BG)
        runs.grid(row=0, column=1, sticky="e")
        self.pack_button = self._button(
            runs,
            tr("VPK만 만들기", "Build VPK only"),
            lambda: self._start_task(pack_only=True),
            secondary=True,
        )
        self.pack_button.pack(side="left", padx=(0, 8))
        self.upload_button = self._button(
            runs,
            tr("Steam 창작마당 업로드", "Upload to Steam Workshop"),
            lambda: self._start_task(pack_only=False),
            secondary=False,
        )
        self.upload_button.pack(side="left")

        log_card = self._card(body)
        log_card.grid(row=3, column=0, sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        log_header = tk.Frame(log_card, bg=CARD)
        log_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 10))
        log_header.grid_columnconfigure(0, weight=1)
        self._section_title(log_header, tr("실행 로그", "Run log")).grid(row=0, column=0, sticky="w")
        tk.Button(
            log_header,
            text=tr("지우기", "Clear"),
            command=self._clear_log,
            bg=CARD,
            fg=MUTED,
            activebackground=CARD,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=FONT_SMALL,
        ).grid(row=0, column=1, sticky="e")
        self.log = scrolledtext.ScrolledText(
            log_card,
            height=10,
            wrap="word",
            state="disabled",
            bg="#101826",
            fg="#D6E2F0",
            insertbackground="white",
            selectbackground="#284A73",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            font=FONT_BODY,
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))

        footer = tk.Frame(self.root, bg="#E8EDF3", height=42)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        self.status_label = tk.Label(
            footer,
            textvariable=self.status,
            bg="#E8EDF3",
            fg=SUCCESS,
            font=FONT_SMALL,
        )
        self.status_label.pack(side="left", padx=24)
        tk.Label(
            footer,
            text=tr("업로드 버튼은 Steam과 GitHub에 실제 변경을 적용합니다.", "The upload button applies real changes to Steam and GitHub."),
            bg="#E8EDF3",
            fg="#475569",
            font=FONT_SMALL,
        ).pack(side="right", padx=24)

    def _label(self, parent, text: str, row: int, anchor: str = "w") -> None:
        tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=FONT_BODY).grid(
            row=row,
            column=0,
            sticky=anchor,
            padx=(18, 10),
            pady=(0, 12),
        )

    def _on_language_change(self) -> None:
        if self.running:
            return
        note_text = "" if self.note_placeholder_active else self.update_note.get(
            "1.0", "end-1c"
        )
        log_text = self.log.get("1.0", "end-1c")
        set_language(self.language.get())
        self._save_settings()
        for child in self.root.winfo_children():
            child.destroy()
        self.root.title(APP_TITLE)
        self.note_placeholder_active = False
        self._build_ui()
        if note_text:
            self.update_note.delete("1.0", "end")
            self.update_note.configure(fg=TEXT)
            self.update_note.insert("1.0", note_text)
        if log_text:
            self.log.configure(state="normal")
            self.log.insert("1.0", log_text)
            self.log.configure(state="disabled")
        mode = self._current_mode()
        if mode is AssetSourceMode.LOCAL:
            self.github_card.grid_remove()
            self.asset_path_entry.configure(textvariable=self.asset_folder)
        elif mode is AssetSourceMode.GITHUB:
            self.github_card.grid()
            self.asset_path_entry.configure(textvariable=self.github_asset_folder)
        self.status.set(tr("준비됨", "Ready"))
        self._refresh_localized_github_status()
        self._apply_availability()

    def _refresh_localized_github_status(self) -> None:
        mode = self._current_mode()
        if mode is AssetSourceMode.LOCAL:
            self.github_status.set(tr("로컬 모드", "Local mode"))
        elif self.pending_store.exists():
            self.github_status.set(
                tr(
                    tr("Steam 업로드 결과 확인 필요", "Steam upload result must be confirmed"),
                    "Steam upload result must be confirmed",
                )
                if self._pending_steam_result_unknown()
                else tr(
                    tr("Steam 업로드 완료 · GitHub push 재시도 필요", "Steam upload complete · GitHub push retry required"),
                    "Steam upload complete · GitHub push retry required",
                )
            )
        elif self.sync_state is SyncState.LOGIN_REQUIRED:
            self.github_status.set(
                tr("GitHub 로그인이 필요합니다.", "GitHub login is required.")
            )
        elif self.sync_state is SyncState.READY and self.session is not None:
            self.github_status.set(
                tr(
                    "최신 상태 · 로그인: {login}",
                    "Up to date · Signed in: {login}",
                    login=self.session.user.login,
                )
            )
        elif self.sync_state in {SyncState.DOWNLOADING, SyncState.CHECKING}:
            self.github_status.set(
                tr(
                    "GitHub 인증과 동기화를 확인하는 중...",
                    "Checking GitHub authentication and sync...",
                )
            )
        else:
            self.github_status.set(
                tr(
                    "저장소 설정을 확인하고 다시 시도하세요.",
                    "Check the repository settings and try again.",
                )
            )

    def _current_mode(self) -> AssetSourceMode | None:
        try:
            return AssetSourceMode(self.asset_source_mode.get())
        except ValueError:
            return None

    def _repository_target(self) -> RepositoryTarget:
        return RepositoryTarget.parse(
            self.github_repository.get(),
            self.github_branch.get(),
            self.github_asset_subdir.get(),
        )

    def _refresh_github_asset_folder(self) -> None:
        try:
            target = self._repository_target()
        except ValueError:
            self.github_asset_folder.set("")
            return
        repository_root = target.clone_root(asset_upload.RUNTIME_PATH)
        self.github_asset_folder.set(str(target.asset_folder(repository_root)))

    def _on_github_target_change(self) -> None:
        self._refresh_github_asset_folder()
        if (
            self._current_mode() is AssetSourceMode.GITHUB
            and not self.pending_store.exists()
        ):
            self.sync_state = SyncState.ERROR
            self.github_status.set(
                tr("저장소 설정이 변경되었습니다. 다시 확인하세요.", "Repository settings changed. Check again.")
            )
            self._apply_availability()


    def _pending_steam_result_unknown(self) -> bool:
        if not self.pending_store.exists():
            return False
        try:
            pending = self.pending_store.load()
        except PendingUploadError:
            return False
        return pending is not None and pending.phase is PendingPhase.STEAM_STARTED
    def _prompt_initial_mode(self) -> None:
        answer = messagebox.askyesnocancel(
            APP_TITLE,
            tr(
                "에셋 관리 방식을 선택하세요.\n\n예: GitHub 동기화 사용\n아니요: 로컬 폴더만 사용",
                "Choose how to manage assets.\n\nYes: Use GitHub sync\nNo: Use a local folder only",
            ),
            parent=self.root,
        )
        if answer is None:
            self.root.destroy()
            return
        mode = AssetSourceMode.GITHUB if answer else AssetSourceMode.LOCAL
        self.asset_source_mode.set(mode.value)
        self._previous_mode = mode.value
        self._save_settings()
        self._apply_mode()

    def _on_mode_change(self) -> None:
        selected = self.asset_source_mode.get()
        if self.pending_store.exists():
            self.asset_source_mode.set(
                self._previous_mode or AssetSourceMode.GITHUB.value
            )
            messagebox.showwarning(
                APP_TITLE,
                tr("미완료 GitHub push를 먼저 재시도해야 모드를 변경할 수 있습니다.", "Retry the unfinished GitHub push before changing modes."),
                parent=self.root,
            )
            return
        self._previous_mode = selected
        self._save_settings()
        self._apply_mode()

    def _apply_mode(self) -> None:
        mode = self._current_mode()
        if mode is None:
            self._apply_availability()
            return
        if mode is AssetSourceMode.LOCAL:
            self.github_card.grid_remove()
            self.asset_path_entry.configure(textvariable=self.asset_folder)
            self.sync_state = SyncState.READY
            self.session = None
            self.github_status.set(tr("로컬 모드", "Local mode"))
        else:
            self.github_card.grid()
            self._refresh_github_asset_folder()
            self.asset_path_entry.configure(textvariable=self.github_asset_folder)
            if self.pending_store.exists():
                self.sync_state = SyncState.PUSH_PENDING
                self.github_status.set(
                    tr("Steam 업로드 결과 확인 필요", "Steam upload result must be confirmed")
                    if self._pending_steam_result_unknown()
                    else tr("Steam 업로드 완료 · GitHub push 재시도 필요", "Steam upload complete · GitHub push retry required")
                )
            else:
                self.sync_state = SyncState.LOGIN_REQUIRED
                self.github_status.set(tr("GitHub 인증과 동기화를 확인하는 중...", "Checking GitHub authentication and sync..."))
                self._start_github_prepare(login=False)
        self._apply_availability()

    def _show_note_placeholder(self) -> None:
        if self.update_note.get("1.0", "end-1c"):
            return
        self.note_placeholder_active = True
        self.update_note.configure(fg="#94A3B8")
        self.update_note.insert("1.0", UPDATE_NOTE_PLACEHOLDER)

    def _on_note_focus_in(self, _event=None) -> None:
        if self.note_placeholder_active:
            self.update_note.delete("1.0", "end")
            self.update_note.configure(fg=TEXT)
            self.note_placeholder_active = False

    def _on_note_focus_out(self, _event=None) -> None:
        if not self.update_note.get("1.0", "end-1c"):
            self._show_note_placeholder()

    def _get_update_note(self) -> str:
        if self.note_placeholder_active:
            return ""
        return self.update_note.get("1.0", "end-1c")

    def _clear_update_note(self) -> None:
        self.update_note.configure(state="normal", fg=TEXT)
        self.update_note.delete("1.0", "end")
        self.note_placeholder_active = False
        self._show_note_placeholder()

    def _choose_asset_folder(self) -> None:
        if self._current_mode() is not AssetSourceMode.LOCAL:
            return
        selected = filedialog.askdirectory(
            title=tr("에셋 폴더 선택", "Select asset folder"), mustexist=True, parent=self.root
        )
        if selected:
            self.asset_folder.set(selected)

    def _choose_output_folder(self) -> None:
        current = Path(os.path.expandvars(self.output_folder.get().strip())).expanduser()
        selected = filedialog.askdirectory(
            title=tr("VPK 출력 폴더 선택", "Select VPK output folder"),
            initialdir=current if current.is_dir() else Path.home(),
            mustexist=True,
            parent=self.root,
        )
        if selected:
            self.output_folder.set(selected)

    def _choose_preview_image(self) -> None:
        current_value = self.preview_path.get().strip()
        current = (
            Path(os.path.expandvars(current_value)).expanduser()
            if current_value
            else None
        )
        initial_directory = (
            current.parent
            if current is not None and current.is_file()
            else Path.home()
        )
        selected = filedialog.askopenfilename(
            title=tr("Workshop 미리보기 이미지 선택", "Select Workshop preview image"),
            initialdir=initial_directory,
            filetypes=(
                (tr("이미지 파일", "Image files"), "*.png *.jpg *.jpeg *.gif"),
                (tr("모든 파일", "All files"), "*.*"),
            ),
            parent=self.root,
        )
        if selected:
            self.preview_path.set(selected)

    def _active_asset_folder(self) -> Path | None:
        if self._current_mode() is AssetSourceMode.GITHUB:
            try:
                target = self._repository_target()
            except ValueError:
                return None
            return target.asset_folder(target.clone_root(asset_upload.RUNTIME_PATH))
        value = self.asset_folder.get().strip()
        return Path(os.path.expandvars(value)).expanduser() if value else None

    def _open_asset_folder(self) -> None:
        folder = self._active_asset_folder()
        if folder is None:
            self._choose_asset_folder()
            return
        if not folder.is_dir():
            messagebox.showerror(
                APP_TITLE,
                tr("에셋 폴더가 존재하지 않습니다.\n\n{path}", "Asset folder does not exist.\n\n{path}", path=folder),
                parent=self.root,
            )
            return
        os.startfile(folder)

    def _open_output_folder(self) -> None:
        if self.last_pack_folder is not None and self.last_pack_folder.is_dir():
            os.startfile(self.last_pack_folder)
            return
        value = self.output_folder.get().strip()
        if not value:
            self._choose_output_folder()
            return
        folder = Path(os.path.expandvars(value)).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(
                APP_TITLE,
                tr("출력 폴더를 열 수 없습니다.\n\n{error}", "Could not open the output folder.\n\n{error}", error=error),
                parent=self.root,
            )
            return
        os.startfile(folder)

    def _parse_options(self) -> UploadOptions:
        try:
            workshop_id = int(self.workshop_id.get().strip())
            chunk_size = int(self.chunk_size.get().strip())
        except ValueError as error:
            raise ValueError(tr("Addon ID와 청크 크기는 숫자로 입력하세요.", "Enter numeric values for the Addon ID and chunk size.")) from error
        if workshop_id < 0:
            raise ValueError(tr("Addon ID는 0 이상의 숫자여야 합니다.", "The Addon ID must be zero or a positive number."))
        if chunk_size < 1:
            raise ValueError(tr("청크 크기는 1 MiB 이상이어야 합니다.", "The chunk size must be at least 1 MiB."))
        title = self.workshop_title.get().strip() or None
        description = self.workshop_description.get().strip() or None
        if title is not None and len(title) > 128:
            raise ValueError(tr("Workshop 제목은 128자 이하여야 합니다.", "The Workshop title must be 128 characters or fewer."))
        if description is not None and len(description) > 8000:
            raise ValueError(tr("Workshop 설명은 8,000자 이하여야 합니다.", "The Workshop description must be 8,000 characters or fewer."))
        local_value = self.asset_folder.get().strip()
        local_folder = (
            Path(os.path.expandvars(local_value)).expanduser().resolve()
            if local_value
            else Path()
        )
        if self._current_mode() is AssetSourceMode.LOCAL and not local_folder.is_dir():
            raise ValueError(tr("에셋 폴더가 존재하지 않습니다: {path}", "Asset folder does not exist: {path}", path=local_folder))
        output_value = self.output_folder.get().strip()
        if not output_value:
            raise ValueError(tr("VPK 출력 폴더를 선택하세요.", "Select a VPK output folder."))
        output_folder = Path(os.path.expandvars(output_value)).expanduser().resolve()
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(tr("출력 폴더를 만들 수 없습니다: {path}", "Could not create the output folder: {path}", path=output_folder)) from error
        preview_value = self.preview_path.get().strip()
        preview_path = (
            Path(os.path.expandvars(preview_value)).expanduser().resolve()
            if preview_value
            else None
        )
        if preview_path is not None and not preview_path.is_file():
            raise ValueError(
                tr(
                    "미리보기 이미지가 존재하지 않습니다: {path}",
                    "Preview image does not exist: {path}",
                    path=preview_path,
                )
            )
        github_target = (
            self._repository_target()
            if self._current_mode() is AssetSourceMode.GITHUB
            else None
        )
        return UploadOptions(
            workshop_id,
            chunk_size,
            title,
            description,
            local_folder,
            output_folder,
            preview_path,
            github_target=github_target,
        )

    def _bundled_git_path(self) -> Path:
        if getattr(sys, "frozen", False):
            return asset_upload.BUNDLE_PATH / "mingit" / "cmd" / "git.exe"
        return asset_upload.SOURCE_PATH / ".vendor" / "MinGit" / "cmd" / "git.exe"

    def _ensure_github_services(self) -> None:
        if self.auth_manager is not None and self.repository_factory is not None:
            return
        config = load_github_app_config(asset_upload.BUNDLE_PATH)
        git_exe = self._bundled_git_path()
        if not git_exe.is_file():
            raise GitHubConfigError(tr("번들 MinGit을 찾을 수 없습니다: {path}", "Bundled MinGit was not found: {path}", path=git_exe))
        runner = GitRunner(git_exe, ensure_askpass(asset_upload.RUNTIME_PATH))

        def repository_factory(
            session: GitHubSession,
            target: RepositoryTarget,
        ) -> AssetRepository:
            return AssetRepository(
                runner,
                target.clone_root(asset_upload.RUNTIME_PATH),
                session.credential,
                remote_url=target.remote_url,
                branch=target.branch,
                asset_subdir=target.asset_subdir,
                progress=lambda state, message, percent: self.events.put(
                    ("github_state", (state, message, percent))
                ),
            )

        self.repository_factory = repository_factory
        self.auth_manager = GitHubAuthManager(
            GitHubApiClient(config.client_id),
            WindowsCredentialStore(),
        )
        self.workflow = UploadWorkflow(
            asset_upload.auto_update, repository_factory, self.pending_store
        )

    def _start_github_prepare(self, *, login: bool) -> None:
        if self.running or self._current_mode() is not AssetSourceMode.GITHUB:
            return
        try:
            target = self._repository_target()
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        self._save_settings()
        self._set_running(True, tr("GitHub 확인 중...", "Checking GitHub..."))
        threading.Thread(
            target=self._run_github_prepare,
            args=(login, target),
            daemon=True,
        ).start()

    def _run_github_prepare(
        self,
        login: bool,
        target: RepositoryTarget,
    ) -> None:
        try:
            self._ensure_github_services()
            if login:
                self.cancel_login.clear()
                session = self.auth_manager.login(
                    target,
                    lambda code: self.events.put(("device_code", code)),
                    self.cancel_login.is_set,
                )
            else:
                session = self.auth_manager.get_valid_session(target)
            if session is None:
                self.events.put(("github_login_required", None))
                return
            sync = self.workflow.prepare_github(session, target)
            self.events.put(("github_ready", (session, sync, target)))
        except Exception as error:
            self.events.put(("log", "\n" + traceback.format_exc()))
            self.events.put(("github_error", str(error)))

    def _github_action(self) -> None:
        if self.pending_store.exists():
            if self._pending_steam_result_unknown():
                self._resolve_pending_steam_result()
            else:
                self._start_pending_retry()
        elif self.sync_state is SyncState.LOGIN_REQUIRED:
            self._start_github_prepare(login=True)
        else:
            self._start_github_prepare(login=False)


    def _resolve_pending_steam_result(self) -> None:
        answer = messagebox.askyesnocancel(
            APP_TITLE,
            tr(
                "Steam Workshop 페이지에서 마지막 업로드 결과를 확인하세요.\n\n성공했으면 '예', 실패했으면 '아니요'를 선택하세요.",
                "Check the latest upload on the Steam Workshop page.\n\nChoose Yes if it succeeded or No if it failed.",
            ),
            icon="warning",
            parent=self.root,
        )
        if answer is None:
            return
        try:
            if not answer:
                self.workflow.resolve_ambiguous_steam(False)
                self.sync_state = SyncState.LOGIN_REQUIRED
                self.github_status.set(tr("Steam 실패 확인 · 다시 업로드할 수 있습니다.", "Steam failure confirmed · You can upload again."))
                self._start_github_prepare(login=False)
                return
            pending = self.pending_store.load()
            if pending is None:
                raise RuntimeError(tr("확인할 Steam 업로드 기록이 없습니다.", "There is no Steam upload record to confirm."))
            workshop_id = pending.workshop_id
            if workshop_id <= 0:
                workshop_id = simpledialog.askinteger(
                    APP_TITLE,
                    tr("성공한 새 Workshop 항목의 Addon ID를 입력하세요.", "Enter the Addon ID of the new Workshop item that succeeded."),
                    minvalue=1,
                    parent=self.root,
                )
                if workshop_id is None:
                    return
            resolved = self.workflow.resolve_ambiguous_steam(True, workshop_id)
        except Exception as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        self.workshop_id.set(str(resolved.workshop_id))
        self._start_pending_retry()
    def _start_pending_retry(self) -> None:
        if self.running:
            return
        self._set_running(True, tr("GitHub push 재시도 중...", "Retrying GitHub push..."))
        threading.Thread(target=self._run_pending_retry, daemon=True).start()

    def _run_pending_retry(self) -> None:
        try:
            self._ensure_github_services()
            pending = self.pending_store.load()
            if pending is None:
                raise RuntimeError("No pending GitHub push was found")
            target = pending.target
            if self.session is not None:
                session = self.auth_manager.validate_session(self.session, target)
            else:
                session = self.auth_manager.get_valid_session(target)
            if session is None:
                self.cancel_login.clear()
                session = self.auth_manager.login(
                    target,
                    lambda code: self.events.put(("device_code", code)),
                    self.cancel_login.is_set,
                )
            result = self.workflow.retry_pending_push(session)
            self.events.put(("push_done", (session, result)))
        except Exception as error:
            self.events.put(("log", "\n" + traceback.format_exc()))
            self.events.put(("task_error", str(error)))

    def _start_task(self, *, pack_only: bool) -> None:
        if self.running:
            return
        mode = self._current_mode()
        if mode is None:
            self._prompt_initial_mode()
            return
        try:
            options = self._parse_options()
            if not pack_only and options.workshop_id == 0 and options.title is None:
                raise ValueError(tr("새 Workshop 항목은 제목을 입력해야 합니다.", "A title is required for a new Workshop item."))
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        if mode is AssetSourceMode.GITHUB and self.sync_state is not SyncState.READY:
            messagebox.showwarning(
                APP_TITLE,
                tr("GitHub 동기화가 최신 상태일 때만 작업할 수 있습니다.", "This action is available only when GitHub sync is up to date."),
                parent=self.root,
            )
            return
        if not pack_only:
            target = (
                tr("새 Steam Workshop 항목", "a new Steam Workshop item")
                if options.workshop_id == 0
                else tr(
                    "Steam Workshop 항목 {workshop_id}",
                    "Steam Workshop item {workshop_id}",
                    workshop_id=options.workshop_id,
                )
            )
            if not messagebox.askyesno(
                APP_TITLE,
                tr("{target}에 VPK 전체를 업로드합니다.\n\n계속할까요?", "The complete VPK will be uploaded to {target}.\n\nContinue?", target=target),
                icon="warning",
                parent=self.root,
            ):
                return
        self._set_running(True, tr("작업 중...", "Working..."))
        self._append_log(
            "\n"
            + "=" * 64
            + "\n"
            + (
                tr("VPK 생성을 시작합니다.\n", "Starting VPK creation.\n")
                if pack_only
                else tr("VPK 생성과 Steam 업로드를 시작합니다.\n", "Starting VPK creation and Steam upload.\n")
            )
        )
        threading.Thread(
            target=self._run_task,
            args=(mode, options, pack_only, self._get_update_note(), self.session),
            daemon=True,
        ).start()

    def _run_task(
        self,
        mode: AssetSourceMode,
        options: UploadOptions,
        pack_only: bool,
        update_note: str,
        session: GitHubSession | None,
    ) -> None:
        writer = QueueWriter(self.events)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                if pack_only:
                    result = self.workflow.build_vpk(
                        mode, options, update_note, session
                    )
                else:
                    result = self.workflow.upload(mode, options, update_note, session)
        except Exception as error:
            self.events.put(("log", "\n" + traceback.format_exc()))
            self.events.put(("task_error", str(error)))
        else:
            self.events.put(("task_done", (pack_only, mode, result)))

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "log":
                    self._append_log(value)
                elif event == "device_code":
                    code: DeviceCode = value
                    webbrowser.open(code.verification_uri)
                    messagebox.showinfo(
                        APP_TITLE,
                        tr(
                            "브라우저에서 GitHub 로그인을 완료하세요.\n\n코드: {code}\n주소: {url}",
                            "Complete the GitHub login in your browser.\n\nCode: {code}\nURL: {url}",
                            code=code.user_code,
                            url=code.verification_uri,
                        ),
                        parent=self.root,
                    )
                elif event == "github_state":
                    self.sync_state, message, percent = value
                    self.github_status.set(message)
                    if percent is None:
                        self.github_progress_frame.grid_remove()
                    else:
                        self.github_progress.set(percent)
                        self.github_progress_text.set(f"{percent}%")
                        self.github_progress_frame.grid()
                elif event == "github_login_required":
                    self.github_progress_frame.grid_remove()
                    self.session = None
                    self.sync_state = SyncState.LOGIN_REQUIRED
                    self.github_status.set(tr("GitHub 로그인이 필요합니다.", "GitHub login is required."))
                    self._set_running(False)
                elif event == "github_ready":
                    self.github_progress_frame.grid_remove()
                    self.session, sync, target = value
                    self.sync_state = sync.state
                    self.github_status.set(
                        tr(
                            "{message} · {target} · 로그인: {login}",
                            "{message} · {target} · Signed in: {login}",
                            message=sync.message,
                            target=target.full_name,
                            login=self.session.user.login,
                        )
                    )
                    self._set_running(False)
                elif event == "github_error":
                    self.github_progress_frame.grid_remove()
                    self.sync_state = SyncState.ERROR
                    self.github_status.set(value)
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, value, parent=self.root)
                elif event == "task_error":
                    if self.pending_store.exists():
                        self.sync_state = SyncState.PUSH_PENDING
                        self.github_status.set(
                            tr("Steam 업로드 결과 확인 필요", "Steam upload result must be confirmed")
                            if self._pending_steam_result_unknown()
                            else tr("Steam 업로드 완료 · GitHub push 재시도 필요", "Steam upload complete · GitHub push retry required")
                        )
                    self._set_running(False)
                    messagebox.showerror(
                        APP_TITLE,
                        tr("작업에 실패했습니다.\n\n{error}", "The operation failed.\n\n{error}", error=value),
                        parent=self.root,
                    )
                elif event == "task_done":
                    pack_only, mode, result = value
                    self._finish_task(pack_only, mode, result)
                elif event == "push_done":
                    self.session, result = value
                    self.sync_state = SyncState.READY
                    self.github_status.set(
                        tr("최신 상태 · 로그인: {login}", "Up to date · Signed in: {login}", login=self.session.user.login)
                    )
                    if result.workshop_id is not None:
                        self.workshop_id.set(str(result.workshop_id))
                    self._clear_update_note()
                    self._set_running(False)
                    messagebox.showinfo(APP_TITLE, result.message, parent=self.root)
        except queue.Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._drain_events)

    def _finish_task(
        self,
        pack_only: bool,
        mode: AssetSourceMode,
        result: WorkflowResult,
    ) -> None:
        if result.pack_folder is not None:
            self.last_pack_folder = Path(result.pack_folder)
        if result.workshop_id is not None:
            self.workshop_id.set(str(result.workshop_id))
        if mode is AssetSourceMode.GITHUB:
            self.sync_state = SyncState.READY
            if self.session is not None:
                self.github_status.set(
                    tr("최신 상태 · 로그인: {login}", "Up to date · Signed in: {login}", login=self.session.user.login)
                )
        if not pack_only:
            self._clear_update_note()
        self._set_running(False)
        detail = result.message
        if self.last_pack_folder is not None:
            detail += tr("\n\n출력: {path}", "\n\nOutput: {path}", path=self.last_pack_folder)
        messagebox.showinfo(APP_TITLE, detail, parent=self.root)

    def _append_log(self, value: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool, status: str | None = None) -> None:
        self.running = running
        if status is not None:
            self.status.set(status)
            self.status_label.configure(fg=ACCENT if running else SUCCESS)
        elif not running:
            self.status.set(tr("준비됨", "Ready"))
            self.status_label.configure(fg=SUCCESS)
        self._apply_availability()

    def _apply_availability(self) -> None:
        mode = self._current_mode()
        if not hasattr(self, "pack_button"):
            return
        if mode is None:
            self.pack_button.configure(state="disabled")
            self.upload_button.configure(state="disabled")
            return
        local_value = self.asset_folder.get().strip()
        local_valid = bool(local_value) and Path(
            os.path.expandvars(local_value)
        ).expanduser().is_dir()
        availability = compute_action_availability(
            mode=mode,
            running=self.running,
            local_folder_valid=local_valid,
            sync_state=self.sync_state,
            pending_exists=self.pending_store.exists(),
        )
        self.pack_button.configure(
            state="normal" if availability.vpk_enabled else "disabled"
        )
        self.upload_button.configure(
            state="normal" if availability.steam_enabled else "disabled"
        )
        mode_state = "normal" if availability.mode_enabled else "disabled"
        self.local_mode_button.configure(state=mode_state)
        self.github_mode_button.configure(state=mode_state)
        common_state = "disabled" if self.running else "normal"
        for button in self.language_buttons:
            button.configure(state=common_state)
        for widget in (
            self.id_entry,
            self.chunk_entry,
            self.title_entry,
            self.description_entry,
            self.output_path_entry,
            self.output_browse_button,
            self.preview_path_entry,
            self.preview_browse_button,
            self.asset_button,
            self.output_button,
        ):
            widget.configure(state=common_state)
        self.update_note.configure(state=common_state)
        target_state = (
            "normal"
            if not self.running and not self.pending_store.exists()
            else "disabled"
        )
        for widget in (
            self.github_repository_entry,
            self.github_branch_entry,
            self.github_asset_subdir_entry,
        ):
            widget.configure(state=target_state)
        if mode is AssetSourceMode.LOCAL:
            self.asset_path_entry.configure(
                textvariable=self.asset_folder, state=common_state
            )
            self.asset_browse_button.configure(state=common_state)
        else:
            self.asset_path_entry.configure(
                textvariable=self.github_asset_folder, state="readonly"
            )
            self.asset_browse_button.configure(state="disabled")
            self.github_action_button.configure(
                state="disabled" if self.running else "normal"
            )
            if availability.retry_push_enabled:
                self.github_action_button.configure(
                    text=tr("Steam 결과 확인 필요", "Confirm Steam result")
                    if self._pending_steam_result_unknown()
                    else tr("GitHub Push 재시도", "Retry GitHub push")
                )
            elif self.sync_state is SyncState.LOGIN_REQUIRED:
                self.github_action_button.configure(text=tr("GitHub 로그인", "GitHub login"))
            else:
                self.github_action_button.configure(text=tr("다시 시도", "Retry"))

    def _on_close(self) -> None:
        if self.running:
            messagebox.showwarning(
                APP_TITLE,
                tr("작업이 진행 중입니다. 완료한 뒤 종료하세요.", "An operation is running. Close the program after it finishes."),
                parent=self.root,
            )
            return
        self._save_settings()
        self.root.destroy()


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def run_pack_only_smoke_test() -> bool:
    if os.environ.get("WORKSHOP_UPLOADER_SMOKE_TEST") != "1":
        return False
    workshop_id = int(os.environ.get("WORKSHOP_UPLOADER_TEST_ID", "9999999999"))
    chunk_size = int(os.environ.get("WORKSHOP_UPLOADER_TEST_CHUNK_MB", "1"))
    asset_folder = Path(os.environ["WORKSHOP_UPLOADER_TEST_ASSET_FOLDER"])
    output_folder = Path(os.environ["WORKSHOP_UPLOADER_TEST_OUTPUT_FOLDER"])
    asset_upload.auto_update(
        workshop_id,
        chunk_size,
        True,
        asset_folder=asset_folder,
        output_folder=output_folder,
        isolated_output=True,
    )
    print("SMOKE TEST OK")
    return True


def main() -> None:
    if run_pack_only_smoke_test():
        return
    enable_dpi_awareness()
    root = tk.Tk()
    WorkshopUploaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
