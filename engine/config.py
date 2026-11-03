from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    raw: dict

    @property
    def swing(self) -> dict:
        return self.raw["swing"]

    @property
    def breakers(self) -> dict:
        return self.raw["breakers"]

    @property
    def market(self) -> dict:
        return self.raw["market"]

    @property
    def capital(self) -> dict:
        return self.raw["capital"]


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    with open(path, "rb") as fh:
        return Config(tomllib.load(fh))
