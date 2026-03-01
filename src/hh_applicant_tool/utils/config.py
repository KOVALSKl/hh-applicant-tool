from __future__ import annotations

import platform
from functools import cache
from os import getenv
from pathlib import Path
from typing import Any

from ..backends import ConfigBackend, FileConfigBackend


@cache
def get_config_path() -> Path:
    match platform.system():
        case "Windows":
            return Path(getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            return Path.home() / "Library" / "Application Support"
        case _:
            return Path(getenv("XDG_CONFIG_HOME", Path.home() / ".config"))


class Config(dict):
    """Конфигурация приложения с подключаемым бэкендом хранения.

    Принимает Path/str (обратная совместимость) или ConfigBackend.
    """

    def __init__(self, source: str | Path | ConfigBackend | None = None) -> None:
        if source is None:
            self._backend = FileConfigBackend(get_config_path())
        elif isinstance(source, ConfigBackend):
            self._backend = source
        else:
            self._backend = FileConfigBackend(Path(source))
        self.load()

    def load(self) -> None:
        if self._backend.exists():
            self.update(self._backend.load())

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.update(*args, **kwargs)
        self._backend.save(dict(self))

    __getitem__ = dict.get

    def __repr__(self) -> str:
        return f"Config({self._backend!r})"
