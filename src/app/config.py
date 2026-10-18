"""Typed settings loaded once at the application boundary."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Self

APP_NAME = "TxnCount"


@dataclass(frozen=True, slots=True)
class Settings:
    debug: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environ is None else environ
        packaged_debug = files("app").joinpath(".debug-mode").is_file()
        debug = values.get("APP_DEBUG", "1" if packaged_debug else "0").strip().lower()
        if debug not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
            raise ValueError("APP_DEBUG must be a boolean: 1/0, true/false, yes/no, on/off")
        return cls(debug=debug in {"1", "true", "yes", "on"})
