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
from tkinter import filedialog, messagebox, scrolledtext, ttk

import app_settings
import asset_upload
from app_settings import AssetSourceMode
from asset_sync import AssetRepository, SyncState
from git_client import GitRunner, ensure_askpass
from github_auth import DeviceCode, GitHubApiClient, GitHubAuthManager, GitHubSession
from github_config import GitHubConfigError, load_github_app_config
from gui_state import compute_action_availability
from pending_upload import PendingUploadStore
from update_notes import UPDATE_NOTE_PLACEHOLDER
from upload_workflow import UploadOptions, UploadWorkflow, WorkflowResult
from windows_credentials import WindowsCredentialStore


APP_TITLE = "CS2 Workshop Uploader"
DEFAULT_WORKSHOP_ID = ""
SETTINGS_PATH = asset_upload.RUNTIME_PATH / "settings.json"
PENDING_PATH = asset_upload.RUNTIME_PATH / "pending_upload.json"
REPOSITORY_ROOT = asset_upload.RUNTIME_PATH / "repos" / "RSS-ZE-ASSET"
GITHUB_ASSET_FOLDER = REPOSITORY_ROOT / "in" / "additional_files"

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
        saved_mode = app_settings.selected_mode(saved)
        if self.pending_store.exists():
            saved_mode = AssetSourceMode.GITHUB
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
        self.github_asset_folder = tk.StringVar(value=str(GITHUB_ASSET_FOLDER))
        self.output_folder = tk.StringVar(
            value=saved.get(
                "output_folder", str(asset_upload.RUNTIME_PATH / "output")
            )
        )
        self.status = tk.StringVar(value="준비됨")
        self.github_status = tk.StringVar(value="GitHub 로그인 필요")
        self.note_placeholder_active = False

        self._configure_window()
        self._build_ui()
        self.asset_folder.trace_add("write", lambda *_args: self._apply_availability())
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if saved_mode is None and not self.pending_store.exists():
            self.root.after(0, self._prompt_initial_mode)
        else:
            self.root.after(0, self._apply_mode)

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("940x980")
        self.root.minsize(850, 900)
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
            "workshop_id": self.workshop_id.get(),
            "workshop_title": self.workshop_title.get(),
            "workshop_description": self.workshop_description.get(),
            "chunk_size_mb": self.chunk_size.get(),
            "asset_folder": self.asset_folder.get(),
            "output_folder": self.output_folder.get(),
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
                f"설정을 저장하지 못했습니다.\n\n{error}",
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
        tk.Label(
            header,
            text="CS2 Workshop Uploader",
            bg=NAVY,
            fg="white",
            font=FONT_TITLE,
        ).pack(anchor="w", padx=28, pady=(20, 2))
        tk.Label(
            header,
            text="로컬 폴더 또는 GitHub 동기화 · VPK v2 · Steam Workshop",
            bg=NAVY,
            fg="#D7E4F5",
            font=FONT_BODY,
        ).pack(anchor="w", padx=30)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)
        settings = self._card(body)
        settings.grid(row=0, column=0, sticky="ew")
        settings.grid_columnconfigure(1, weight=1)
        self._section_title(settings, "업로드 설정").grid(
            row=0, column=0, columnspan=5, sticky="w", padx=18, pady=(16, 12)
        )

        self._label(settings, "에셋 관리", 1)
        modes = tk.Frame(settings, bg=CARD)
        modes.grid(row=1, column=1, columnspan=4, sticky="w", pady=(0, 12))
        self.local_mode_button = tk.Radiobutton(
            modes,
            text="로컬 폴더",
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
            text="GitHub 동기화",
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
            text="청크 크기 (MiB)",
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

        self._label(settings, "Workshop 제목", 3)
        self.title_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_title,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.title_entry.grid(
            row=3, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )
        self._label(settings, "Workshop 설명", 4)
        self.description_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_description,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.description_entry.grid(
            row=4, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, "에셋 폴더", 5)
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
            settings, "찾아보기", self._choose_asset_folder, secondary=True
        )
        self.asset_browse_button.grid(
            row=5, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, "출력 폴더", 6)
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
            settings, "찾아보기", self._choose_output_folder, secondary=True
        )
        self.output_browse_button.grid(
            row=6, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        self._label(settings, "업데이트 내역", 7, anchor="nw")
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
            row=7, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 14)
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
            text="GitHub 에셋 동기화",
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
            self.github_card, "GitHub 로그인", self._github_action, secondary=True
        )
        self.github_action_button.grid(row=0, column=1, padx=(8, 18), pady=13)

        actions = tk.Frame(body, bg=BG)
        actions.grid(row=2, column=0, sticky="ew", pady=14)
        actions.grid_columnconfigure(0, weight=1)
        folders = tk.Frame(actions, bg=BG)
        folders.grid(row=0, column=0, sticky="w")
        self.asset_button = self._button(
            folders, "에셋 폴더 열기", self._open_asset_folder, secondary=True
        )
        self.asset_button.pack(side="left", padx=(0, 8))
        self.output_button = self._button(
            folders, "출력 폴더 열기", self._open_output_folder, secondary=True
        )
        self.output_button.pack(side="left")
        runs = tk.Frame(actions, bg=BG)
        runs.grid(row=0, column=1, sticky="e")
        self.pack_button = self._button(
            runs,
            "VPK만 만들기",
            lambda: self._start_task(pack_only=True),
            secondary=True,
        )
        self.pack_button.pack(side="left", padx=(0, 8))
        self.upload_button = self._button(
            runs,
            "Steam 창작마당 업로드",
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
        self._section_title(log_header, "실행 로그").grid(row=0, column=0, sticky="w")
        tk.Button(
            log_header,
            text="지우기",
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
            text="업로드 버튼은 Steam과 GitHub에 실제 변경을 적용합니다.",
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

    def _current_mode(self) -> AssetSourceMode | None:
        try:
            return AssetSourceMode(self.asset_source_mode.get())
        except ValueError:
            return None

    def _prompt_initial_mode(self) -> None:
        answer = messagebox.askyesnocancel(
            APP_TITLE,
            (
                "에셋 관리 방식을 선택하세요.\n\n"
                "예: GitHub 동기화 사용\n"
                "아니요: 로컬 폴더만 사용"
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
                "미완료 GitHub push를 먼저 재시도해야 모드를 변경할 수 있습니다.",
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
            self.github_status.set("로컬 모드")
        else:
            self.github_card.grid()
            self.asset_path_entry.configure(textvariable=self.github_asset_folder)
            if self.pending_store.exists():
                self.sync_state = SyncState.PUSH_PENDING
                self.github_status.set("Steam 업로드 완료 · GitHub push 재시도 필요")
            else:
                self.sync_state = SyncState.LOGIN_REQUIRED
                self.github_status.set("GitHub 인증과 동기화를 확인하는 중...")
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
            title="에셋 폴더 선택", mustexist=True, parent=self.root
        )
        if selected:
            self.asset_folder.set(selected)

    def _choose_output_folder(self) -> None:
        current = Path(os.path.expandvars(self.output_folder.get().strip())).expanduser()
        selected = filedialog.askdirectory(
            title="VPK 출력 폴더 선택",
            initialdir=current if current.is_dir() else Path.home(),
            mustexist=True,
            parent=self.root,
        )
        if selected:
            self.output_folder.set(selected)

    def _active_asset_folder(self) -> Path | None:
        if self._current_mode() is AssetSourceMode.GITHUB:
            return GITHUB_ASSET_FOLDER
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
                f"에셋 폴더가 존재하지 않습니다.\n\n{folder}",
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
                f"출력 폴더를 열 수 없습니다.\n\n{error}",
                parent=self.root,
            )
            return
        os.startfile(folder)

    def _parse_options(self) -> UploadOptions:
        try:
            workshop_id = int(self.workshop_id.get().strip())
            chunk_size = int(self.chunk_size.get().strip())
        except ValueError as error:
            raise ValueError("Addon ID와 청크 크기는 숫자로 입력하세요.") from error
        if workshop_id < 0:
            raise ValueError("Addon ID는 0 이상의 숫자여야 합니다.")
        if chunk_size < 1:
            raise ValueError("청크 크기는 1 MiB 이상이어야 합니다.")
        title = self.workshop_title.get().strip() or None
        description = self.workshop_description.get().strip() or None
        if title is not None and len(title) > 128:
            raise ValueError("Workshop 제목은 128자 이하여야 합니다.")
        if description is not None and len(description) > 8000:
            raise ValueError("Workshop 설명은 8,000자 이하여야 합니다.")
        local_value = self.asset_folder.get().strip()
        local_folder = (
            Path(os.path.expandvars(local_value)).expanduser().resolve()
            if local_value
            else Path()
        )
        if self._current_mode() is AssetSourceMode.LOCAL and not local_folder.is_dir():
            raise ValueError(f"에셋 폴더가 존재하지 않습니다: {local_folder}")
        output_value = self.output_folder.get().strip()
        if not output_value:
            raise ValueError("VPK 출력 폴더를 선택하세요.")
        output_folder = Path(os.path.expandvars(output_value)).expanduser().resolve()
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"출력 폴더를 만들 수 없습니다: {output_folder}") from error
        return UploadOptions(
            workshop_id,
            chunk_size,
            title,
            description,
            local_folder,
            output_folder,
            None,
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
            raise GitHubConfigError(f"번들 MinGit을 찾을 수 없습니다: {git_exe}")
        runner = GitRunner(git_exe, ensure_askpass(asset_upload.RUNTIME_PATH))

        def repository_factory(session: GitHubSession) -> AssetRepository:
            return AssetRepository(
                runner,
                REPOSITORY_ROOT,
                session.credential,
                progress=lambda state, message: self.events.put(
                    ("github_state", (state, message))
                ),
            )

        self.repository_factory = repository_factory
        self.auth_manager = GitHubAuthManager(
            GitHubApiClient(config.client_id, config.repository_id),
            WindowsCredentialStore(),
        )
        self.workflow = UploadWorkflow(
            asset_upload.auto_update, repository_factory, self.pending_store
        )

    def _start_github_prepare(self, *, login: bool) -> None:
        if self.running or self._current_mode() is not AssetSourceMode.GITHUB:
            return
        self._set_running(True, "GitHub 확인 중...")
        threading.Thread(
            target=self._run_github_prepare, args=(login,), daemon=True
        ).start()

    def _run_github_prepare(self, login: bool) -> None:
        try:
            self._ensure_github_services()
            if login:
                self.cancel_login.clear()
                session = self.auth_manager.login(
                    lambda code: self.events.put(("device_code", code)),
                    self.cancel_login.is_set,
                )
            else:
                session = self.auth_manager.get_valid_session()
            if session is None:
                self.events.put(("github_login_required", None))
                return
            sync = self.workflow.prepare_github(session)
            self.events.put(("github_ready", (session, sync)))
        except Exception as error:
            self.events.put(("log", "\n" + traceback.format_exc()))
            self.events.put(("github_error", str(error)))

    def _github_action(self) -> None:
        if self.pending_store.exists():
            self._start_pending_retry()
        elif self.sync_state is SyncState.LOGIN_REQUIRED:
            self._start_github_prepare(login=True)
        else:
            self._start_github_prepare(login=False)

    def _start_pending_retry(self) -> None:
        if self.running:
            return
        self._set_running(True, "GitHub push 재시도 중...")
        threading.Thread(target=self._run_pending_retry, daemon=True).start()

    def _run_pending_retry(self) -> None:
        try:
            self._ensure_github_services()
            session = self.session or self.auth_manager.get_valid_session()
            if session is None:
                raise RuntimeError("GitHub 로그인이 필요합니다.")
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
                raise ValueError("새 Workshop 항목은 제목을 입력해야 합니다.")
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return
        if mode is AssetSourceMode.GITHUB and self.sync_state is not SyncState.READY:
            messagebox.showwarning(
                APP_TITLE,
                "GitHub 동기화가 최신 상태일 때만 작업할 수 있습니다.",
                parent=self.root,
            )
            return
        if not pack_only:
            target = (
                "새 Steam Workshop 항목"
                if options.workshop_id == 0
                else f"Steam Workshop 항목 {options.workshop_id}"
            )
            if not messagebox.askyesno(
                APP_TITLE,
                f"{target}에 VPK 전체를 업로드합니다.\n\n계속할까요?",
                icon="warning",
                parent=self.root,
            ):
                return
        self._set_running(True, "작업 중...")
        self._append_log(
            "\n"
            + "=" * 64
            + "\n"
            + (
                "VPK 생성을 시작합니다.\n"
                if pack_only
                else "VPK 생성과 Steam 업로드를 시작합니다.\n"
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
                        (
                            "브라우저에서 GitHub 로그인을 완료하세요.\n\n"
                            f"코드: {code.user_code}\n"
                            f"주소: {code.verification_uri}"
                        ),
                        parent=self.root,
                    )
                elif event == "github_state":
                    self.sync_state, message = value
                    self.github_status.set(message)
                elif event == "github_login_required":
                    self.session = None
                    self.sync_state = SyncState.LOGIN_REQUIRED
                    self.github_status.set("GitHub 로그인이 필요합니다.")
                    self._set_running(False)
                elif event == "github_ready":
                    self.session, sync = value
                    self.sync_state = sync.state
                    self.github_status.set(
                        f"{sync.message} · 로그인: {self.session.user.login}"
                    )
                    self._set_running(False)
                elif event == "github_error":
                    self.sync_state = SyncState.ERROR
                    self.github_status.set(value)
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, value, parent=self.root)
                elif event == "task_error":
                    if self.pending_store.exists():
                        self.sync_state = SyncState.PUSH_PENDING
                        self.github_status.set(
                            "Steam 업로드 완료 · GitHub push 재시도 필요"
                        )
                    self._set_running(False)
                    messagebox.showerror(
                        APP_TITLE,
                        f"작업에 실패했습니다.\n\n{value}",
                        parent=self.root,
                    )
                elif event == "task_done":
                    pack_only, mode, result = value
                    self._finish_task(pack_only, mode, result)
                elif event == "push_done":
                    self.session, result = value
                    self.sync_state = SyncState.READY
                    self.github_status.set(
                        f"최신 상태 · 로그인: {self.session.user.login}"
                    )
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
                    f"최신 상태 · 로그인: {self.session.user.login}"
                )
        if not pack_only:
            self._clear_update_note()
        self._set_running(False)
        detail = result.message
        if self.last_pack_folder is not None:
            detail += f"\n\n출력: {self.last_pack_folder}"
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
            self.status.set("준비됨")
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
        for widget in (
            self.id_entry,
            self.chunk_entry,
            self.title_entry,
            self.description_entry,
            self.output_path_entry,
            self.output_browse_button,
            self.asset_button,
            self.output_button,
        ):
            widget.configure(state=common_state)
        self.update_note.configure(state=common_state)
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
                self.github_action_button.configure(text="GitHub Push 재시도")
            elif self.sync_state is SyncState.LOGIN_REQUIRED:
                self.github_action_button.configure(text="GitHub 로그인")
            else:
                self.github_action_button.configure(text="다시 시도")

    def _on_close(self) -> None:
        if self.running:
            messagebox.showwarning(
                APP_TITLE,
                "작업이 진행 중입니다. 완료한 뒤 종료하세요.",
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
