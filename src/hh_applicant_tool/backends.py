"""Абстракции бэкендов хранения конфигурации и cookies."""

from __future__ import annotations

import io
import json
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, runtime_checkable


# ── Протоколы ────────────────────────────────────────────────────────


@runtime_checkable
class ConfigBackend(Protocol):
    """Интерфейс чтения/записи конфигурации (токены, настройки)."""

    def exists(self) -> bool: ...
    def load(self) -> dict[str, Any]: ...
    def save(self, data: dict[str, Any]) -> None: ...


@runtime_checkable
class CookieBackend(Protocol):
    """Интерфейс чтения/записи cookies в формате Netscape."""

    def exists(self) -> bool: ...
    def load_to_jar(self, jar: MozillaCookieJar) -> None: ...
    def save_from_text(self, text: str) -> None: ...


# ── Файловые реализации (по умолчанию) ──────────────────────────────


class FileConfigBackend:
    """Хранение конфигурации в JSON-файле."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = Lock()

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with self._lock:
            with self._path.open("r", encoding="utf-8", errors="replace") as f:
                return json.load(f)

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(exist_ok=True, parents=True)
        with self._lock:
            with self._path.open("w+", encoding="utf-8", errors="replace") as fp:
                json.dump(data, fp, indent=2, sort_keys=True)


class FileCookieBackend:
    """Хранение cookies в файле формата Netscape (MozillaCookieJar)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def exists(self) -> bool:
        return self._path.exists()

    def load_to_jar(self, jar: MozillaCookieJar) -> None:
        if not self._path.exists():
            return
        jar.filename = str(self._path)
        jar.load(ignore_discard=True, ignore_expires=True)

    def save_from_text(self, text: str) -> None:
        self._path.parent.mkdir(exist_ok=True, parents=True)
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(text)
