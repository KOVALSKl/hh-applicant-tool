from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .utils import json


class ConfigBackend(Protocol):
    """Контракт backend для хранения конфигурации пользователя."""

    def exists(self) -> bool: ...

    def load(self) -> dict[str, Any]: ...

    def save(self, data: dict[str, Any]) -> None: ...


class CookieBackend(Protocol):
    """Контракт backend для хранения cookies пользователя."""

    def exists(self) -> bool: ...

    def save_from_text(self, cookies_text: str) -> None: ...


class FileConfigBackend:
    """Файловый backend `config.json`."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        return dict(json.loads(raw))

    def save(self, data: dict[str, Any]) -> None:
        current = self.load()
        current.update(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class FileCookieBackend:
    """Файловый backend `cookies.txt`."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def exists(self) -> bool:
        return self._path.exists()

    def save_from_text(self, cookies_text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(cookies_text, encoding="utf-8")
