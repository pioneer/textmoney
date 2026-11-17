"""Typed settings loaded once at the application boundary."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Self

APP_NAME = "TextMoney"


def _boolean_setting(values: Mapping[str, str], key: str, default: str = "0") -> bool:
    value = values.get(key, default).strip().lower()
    if value not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
        raise ValueError(f"{key} must be a boolean: 1/0, true/false, yes/no, on/off")
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    debug: bool = False
    mobile_preview: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environ is None else environ
        packaged_debug = files("app").joinpath(".debug-mode").is_file()
        return cls(
            debug=_boolean_setting(values, "APP_DEBUG", "1" if packaged_debug else "0"),
            mobile_preview=_boolean_setting(values, "APP_MOBILE_PREVIEW"),
        )
