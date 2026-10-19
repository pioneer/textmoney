"""Read release identity from packaged metadata or the source project."""

import json
import tomllib
from importlib.resources import files
from pathlib import Path


def release_identity() -> tuple[str, int]:
    project_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not project_path.is_file():
        metadata = files("app").joinpath(".release.json")
        values = json.loads(metadata.read_text(encoding="utf-8"))
        version, build = values["version"], values["build_number"]
    else:
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        version = project["project"]["version"]
        build = project["tool"]["flet"]["build_number"]
    if not isinstance(version, str) or not version or type(build) is not int or build < 1:
        raise ValueError("Invalid application version metadata.")
    return version, build
