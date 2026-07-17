from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import asset_upload


APP_TITLE = "CS2 Workshop Uploader"
DEFAULT_WORKSHOP_ID = ""
SETTINGS_PATH = asset_upload.RUNTIME_PATH / "settings.json"
SETTINGS_KEYS = (
    "workshop_id",
    "workshop_title",
    "workshop_description",
    "chunk_size_mb",
    "asset_folder",
    "output_folder",
)

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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key in SETTINGS_KEYS
        if isinstance((value := data.get(key)), str)
    }


def save_settings(settings: dict[str, str], path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


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

        saved_settings = load_settings()
        self.workshop_id = tk.StringVar(
            value=saved_settings.get("workshop_id", DEFAULT_WORKSHOP_ID)
        )
        self.workshop_title = tk.StringVar(
            value=saved_settings.get("workshop_title", "")
        )
        self.workshop_description = tk.StringVar(
            value=saved_settings.get("workshop_description", "")
        )
        self.chunk_size = tk.StringVar(
            value=saved_settings.get(
                "chunk_size_mb", str(asset_upload.DEFAULT_CHUNK_SIZE_MB)
            )
        )
        self.asset_folder = tk.StringVar(
            value=saved_settings.get("asset_folder", "")
        )
        self.output_folder = tk.StringVar(
            value=saved_settings.get(
                "output_folder", str(asset_upload.RUNTIME_PATH / "output")
            )
        )
        self.status = tk.StringVar(value="준비됨")

        self._configure_window()
        self._build_ui()
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("900x900")
        self.root.minsize(820, 840)
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
        return {
            "workshop_id": self.workshop_id.get(),
            "workshop_title": self.workshop_title.get(),
            "workshop_description": self.workshop_description.get(),
            "chunk_size_mb": self.chunk_size.get(),
            "asset_folder": self.asset_folder.get(),
            "output_folder": self.output_folder.get(),
        }

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
            text="EXE 하나로 경로 선택 · VPK v2 멀티청크 · 기본 100 MiB",
            bg=NAVY,
            fg="#D7E4F5",
            font=FONT_BODY,
        ).pack(anchor="w", padx=30)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        settings = self._card(body)
        settings.grid(row=0, column=0, sticky="ew")
        settings.grid_columnconfigure(1, weight=1)

        self._section_title(settings, "Steam 창작마당 업로드 설정").grid(
            row=0, column=0, columnspan=5, sticky="w", padx=18, pady=(16, 12)
        )

        tk.Label(
            settings,
            text="Workshop Addon ID",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=1, column=0, sticky="w", padx=(18, 10), pady=(0, 14))
        self.id_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_id,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.id_entry.grid(
            row=1, column=1, sticky="ew", padx=(0, 20), pady=(0, 14)
        )

        tk.Label(
            settings,
            text="청크 크기 (MiB)",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=1, column=2, sticky="w", padx=(0, 10), pady=(0, 14))
        self.chunk_entry = ttk.Spinbox(
            settings,
            from_=1,
            to=2048,
            textvariable=self.chunk_size,
            width=8,
            style="Uploader.TSpinbox",
            font=FONT_BODY,
        )
        self.chunk_entry.grid(
            row=1, column=3, sticky="e", padx=(0, 18), pady=(0, 14)
        )

        tk.Label(
            settings,
            text="Workshop 제목",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=2, column=0, sticky="w", padx=(18, 10), pady=(0, 12))
        self.title_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_title,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.title_entry.grid(
            row=2, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )

        tk.Label(
            settings,
            text="Workshop 설명",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=3, column=0, sticky="w", padx=(18, 10), pady=(0, 12))
        self.description_entry = ttk.Entry(
            settings,
            textvariable=self.workshop_description,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.description_entry.grid(
            row=3, column=1, columnspan=4, sticky="ew", padx=(0, 18), pady=(0, 12)
        )

        tk.Label(
            settings,
            text="에셋 폴더",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=4, column=0, sticky="w", padx=(18, 10), pady=(0, 12))
        self.asset_path_entry = ttk.Entry(
            settings,
            textvariable=self.asset_folder,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.asset_path_entry.grid(
            row=4, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 12)
        )
        self.asset_browse_button = self._button(
            settings, "찾아보기", self._choose_asset_folder, secondary=True
        )
        self.asset_browse_button.grid(
            row=4, column=4, sticky="e", padx=(0, 18), pady=(0, 12)
        )

        tk.Label(
            settings,
            text="출력 폴더",
            bg=CARD,
            fg=TEXT,
            font=FONT_BODY,
        ).grid(row=5, column=0, sticky="w", padx=(18, 10), pady=(0, 16))
        self.output_path_entry = ttk.Entry(
            settings,
            textvariable=self.output_folder,
            style="Uploader.TEntry",
            font=FONT_BODY,
        )
        self.output_path_entry.grid(
            row=5, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 16)
        )
        self.output_browse_button = self._button(
            settings, "찾아보기", self._choose_output_folder, secondary=True
        )
        self.output_browse_button.grid(
            row=5, column=4, sticky="e", padx=(0, 18), pady=(0, 16)
        )

        tk.Label(
            settings,
            text=(
                "기존 항목은 Addon ID를 입력해 업데이트합니다. "
                "Addon ID를 0으로 입력하면 새 창작마당 항목을 생성합니다."
            ),
            bg=CARD,
            fg="#475569",
            justify="left",
            anchor="w",
            font=FONT_SMALL,
        ).grid(
            row=6,
            column=0,
            columnspan=5,
            sticky="ew",
            padx=18,
            pady=(0, 16),
        )

        actions = tk.Frame(body, bg=BG)
        actions.grid(row=1, column=0, sticky="ew", pady=14)
        actions.grid_columnconfigure(0, weight=1)

        folder_actions = tk.Frame(actions, bg=BG)
        folder_actions.grid(row=0, column=0, sticky="w")
        self.asset_button = self._button(
            folder_actions, "에셋 폴더 열기", self._open_asset_folder, secondary=True
        )
        self.asset_button.pack(side="left", padx=(0, 8))
        self.output_button = self._button(
            folder_actions, "출력 폴더 열기", self._open_output_folder, secondary=True
        )
        self.output_button.pack(side="left")

        run_actions = tk.Frame(actions, bg=BG)
        run_actions.grid(row=0, column=1, sticky="e")
        self.pack_button = self._button(
            run_actions,
            "VPK만 만들기",
            lambda: self._start_task(pack_only=True),
            secondary=True,
        )
        self.pack_button.pack(side="left", padx=(0, 8))
        self.upload_button = self._button(
            run_actions,
            "Steam 창작마당 업로드",
            lambda: self._start_task(pack_only=False),
            secondary=False,
        )
        self.upload_button.pack(side="left")

        log_card = self._card(body)
        log_card.grid(row=2, column=0, sticky="nsew")
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
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=FONT_SMALL,
        ).grid(row=0, column=1, sticky="e")

        self.log = scrolledtext.ScrolledText(
            log_card,
            height=12,
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
            text="파란 버튼은 VPK 패킹 후 Steam 창작마당 항목을 실제로 업데이트합니다.",
            bg="#E8EDF3",
            fg="#475569",
            font=FONT_SMALL,
        ).pack(side="right", padx=24)

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )

    def _section_title(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=CARD,
            fg=TEXT,
            font=FONT_SECTION,
        )

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        *,
        secondary: bool,
    ) -> tk.Button:
        if secondary:
            background = CARD
            foreground = TEXT
            active_background = "#E7EDF5"
            border = BORDER
        else:
            background = ACCENT
            foreground = "white"
            active_background = ACCENT_ACTIVE
            border = ACCENT

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=active_background,
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

    def _choose_asset_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="패킹할 에셋 폴더 선택",
            mustexist=True,
            parent=self.root,
        )
        if selected:
            self.asset_folder.set(selected)

    def _choose_output_folder(self) -> None:
        current = Path(
            os.path.expandvars(self.output_folder.get().strip())
        ).expanduser()
        initial_directory = current if current.is_dir() else Path.home()
        selected = filedialog.askdirectory(
            title="VPK 출력 폴더 선택",
            initialdir=initial_directory,
            mustexist=True,
            parent=self.root,
        )
        if selected:
            self.output_folder.set(selected)

    def _open_asset_folder(self) -> None:
        value = self.asset_folder.get().strip()
        if not value:
            self._choose_asset_folder()
            return
        folder = Path(os.path.expandvars(value)).expanduser()
        if not folder.is_dir():
            messagebox.showerror(
                APP_TITLE, "선택한 에셋 폴더가 존재하지 않습니다.", parent=self.root
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

    def _parse_settings(
        self,
    ) -> tuple[int, int, str | None, str | None, Path, Path]:
        workshop_id_text = self.workshop_id.get().strip()
        if not workshop_id_text:
            raise ValueError("Addon ID를 입력하세요.")
        try:
            workshop_id = int(workshop_id_text)
        except ValueError as error:
            raise ValueError("Addon ID는 숫자로 입력하세요.") from error
        try:
            chunk_size = int(self.chunk_size.get().strip())
        except ValueError as error:
            raise ValueError("청크 크기는 숫자로 입력하세요.") from error

        if workshop_id < 0:
            raise ValueError("Addon ID는 0 이상의 숫자여야 합니다.")
        if chunk_size < 1:
            raise ValueError("청크 크기는 1 MiB 이상이어야 합니다.")

        workshop_title = self.workshop_title.get().strip() or None
        workshop_description = self.workshop_description.get().strip() or None
        if workshop_title is not None and len(workshop_title) > 128:
            raise ValueError("Workshop 제목은 128자 이하여야 합니다.")
        if workshop_description is not None and len(workshop_description) > 8000:
            raise ValueError("Workshop 설명은 8,000자 이하여야 합니다.")

        asset_value = self.asset_folder.get().strip()
        if not asset_value:
            raise ValueError("패킹할 에셋 폴더를 선택하세요.")
        asset_folder = Path(os.path.expandvars(asset_value)).expanduser().resolve()
        if not asset_folder.is_dir():
            raise ValueError(f"에셋 폴더가 존재하지 않습니다: {asset_folder}")

        output_value = self.output_folder.get().strip()
        if not output_value:
            raise ValueError("VPK 출력 폴더를 선택하세요.")
        output_folder = Path(os.path.expandvars(output_value)).expanduser().resolve()
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"출력 폴더를 만들 수 없습니다: {output_folder}") from error

        return (
            workshop_id,
            chunk_size,
            workshop_title,
            workshop_description,
            asset_folder,
            output_folder,
        )

    def _start_task(self, *, pack_only: bool) -> None:
        if self.running:
            return
        try:
            (
                workshop_id,
                chunk_size,
                workshop_title,
                workshop_description,
                asset_folder,
                output_folder,
            ) = self._parse_settings()
            if not pack_only and workshop_id == 0 and workshop_title is None:
                raise ValueError("새 창작마당 항목은 Workshop 제목을 입력해야 합니다.")
        except ValueError as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self.root)
            return

        if not pack_only:
            target = (
                "새 Steam 창작마당 항목"
                if workshop_id == 0
                else f"Steam 창작마당 항목 {workshop_id}"
            )
            confirmed = messagebox.askyesno(
                APP_TITLE,
                (
                    f"{target}에 멀티청크 VPK 전체를 실제로 업로드합니다.\n\n"
                    "Steam이 실행 중이고 해당 항목을 수정할 권한이 있어야 합니다.\n"
                    "계속할까요?"
                ),
                icon="warning",
                parent=self.root,
            )
            if not confirmed:
                return

        self._set_running(True)
        self._append_log(
            "\n"
            + ("=" * 64)
            + "\n"
            + (
                "패킹 테스트를 시작합니다.\n"
                if pack_only
                else "VPK 패킹 및 Steam 창작마당 업로드를 시작합니다.\n"
            )
        )

        worker = threading.Thread(
            target=self._run_task,
            args=(
                workshop_id,
                chunk_size,
                pack_only,
                workshop_title,
                workshop_description,
                asset_folder,
                output_folder,
            ),
            daemon=True,
        )
        worker.start()

    def _run_task(
        self,
        workshop_id: int,
        chunk_size: int,
        pack_only: bool,
        workshop_title: str | None,
        workshop_description: str | None,
        asset_folder: Path,
        output_folder: Path,
    ) -> None:
        writer = QueueWriter(self.events)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                pack_folder = asset_upload.auto_update(
                    workshop_id,
                    chunk_size,
                    pack_only,
                    asset_folder=asset_folder,
                    output_folder=output_folder,
                    isolated_output=True,
                    workshop_title=workshop_title,
                    workshop_description=workshop_description,
                )
        except Exception as error:
            self.events.put(("log", "\n" + traceback.format_exc()))
            self.events.put(("error", str(error)))
        else:
            self.events.put(("done", (pack_only, str(pack_folder))))

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "log":
                    self._append_log(value)
                elif event == "error":
                    self._set_running(False)
                    self.status.set("실패")
                    self.status_label.configure(fg=DANGER)
                    messagebox.showerror(
                        APP_TITLE,
                        f"작업에 실패했습니다.\n\n{value}",
                        parent=self.root,
                    )
                elif event == "done":
                    pack_only, pack_folder_value = value
                    self.last_pack_folder = Path(pack_folder_value)
                    self._set_running(False)
                    self.status.set("완료")
                    self.status_label.configure(fg=SUCCESS)
                    message = (
                        "멀티청크 VPK 패킹이 완료되었습니다."
                        if pack_only
                        else "Steam 창작마당 업로드가 완료되었습니다."
                    )
                    messagebox.showinfo(
                        APP_TITLE,
                        f"{message}\n\n출력: {self.last_pack_folder}",
                        parent=self.root,
                    )
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_events)

    def _append_log(self, value: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for widget in (
            self.id_entry,
            self.chunk_entry,
            self.title_entry,
            self.description_entry,
            self.asset_path_entry,
            self.output_path_entry,
            self.asset_browse_button,
            self.output_browse_button,
            self.asset_button,
            self.output_button,
            self.pack_button,
            self.upload_button,
        ):
            widget.configure(state=state)

        if running:
            self.status.set("작업 중...")
            self.status_label.configure(fg=ACCENT)
        else:
            self.status.set("준비됨")

    def _on_close(self) -> None:
        if self.running:
            messagebox.showwarning(
                APP_TITLE,
                "작업이 진행 중입니다. 완료된 후 종료하세요.",
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
