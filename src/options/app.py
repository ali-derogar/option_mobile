"""BeeWare wrapper that shows the Options web dashboard on mobile."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import toga
from toga.style import Pack
from toga.style.pack import COLUMN


def _runtime_root(app: toga.App) -> Path:
    paths = getattr(app, "paths", None)
    data_path = getattr(paths, "data", None)
    if data_path:
        return Path(data_path)
    return Path(tempfile.gettempdir()) / "options"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class OptionsApp(toga.App):
    """Mobile shell for the same FastAPI + HTML/CSS/JS dashboard as desktop."""

    def startup(self) -> None:
        self._configure_environment()
        self._port = _free_port()
        self._url = f"http://127.0.0.1:{self._port}"

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.webview: toga.WebView | None = None
        self.status_label = toga.Label(
            "در حال راه‌اندازی داشبورد...",
            style=Pack(margin=12, color="#94a3b8", background_color="#0b1120"),
        )
        self.main_window.content = toga.Box(
            children=[self.status_label],
            style=Pack(direction=COLUMN, flex=1, background_color="#0b1120"),
        )
        self.main_window.show()

        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._load_when_ready, daemon=True).start()

    def _configure_environment(self) -> None:
        runtime_root = _runtime_root(self)
        runtime_root.mkdir(parents=True, exist_ok=True)
        data_root = runtime_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        database_path = data_root / "tsetmc_options.db"
        legacy_database_path = Path.home() / "data" / "tsetmc_options.db"
        if not database_path.exists() and legacy_database_path.exists():
            try:
                database_path.write_bytes(legacy_database_path.read_bytes())
            except OSError:
                pass
        os.environ.setdefault("OPTIONS_RUNTIME_ROOT", str(runtime_root))
        os.environ.setdefault("DATABASE_PATH", str(database_path))
        os.environ.setdefault("DATA_DIR", str(data_root / "exports"))
        os.environ["WEB_OPEN_BROWSER"] = "0"

    def _serve(self) -> None:
        import uvicorn

        uvicorn.run(
            "options.backend.api.main:app",
            host="127.0.0.1",
            port=self._port,
            reload=False,
            log_level="warning",
        )

    def _load_when_ready(self) -> None:
        for _ in range(80):
            if self._server_ready():
                self.loop.call_soon_threadsafe(self._show_dashboard)
                return
            time.sleep(0.15)
        self.loop.call_soon_threadsafe(self._show_error)

    def _server_ready(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=0.2):
                return True
        except OSError:
            return False

    def _show_dashboard(self) -> None:
        if self.webview is None:
            self.webview = toga.WebView(url=self._url, style=Pack(flex=1))
        else:
            self.webview.url = self._url
        self.main_window.content = self.webview

    def _show_error(self) -> None:
        self.status_label.text = "خطا در راه‌اندازی داشبورد. برنامه را دوباره باز کنید."


def main() -> OptionsApp:
    return OptionsApp("options", "com.tsetmc.options")
