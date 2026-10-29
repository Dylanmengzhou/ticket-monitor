from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    fetch_timeout: int = 150
    login_timeout: int = 600
    lock_timeout: int = 45

    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> "Settings":
        root = data_dir or os.environ.get("FLIGHT_MCP_DATA_DIR")
        root = Path(root).expanduser() if root else Path.home() / ".local/share/flight-agent-mcp"
        return cls(root.resolve())

    def prepare(self) -> None:
        for path in (self.data_dir, self.profile_root, self.work_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flights.sqlite3"

    @property
    def profile_root(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def profile_path(self) -> Path:
        return self.profile_for("ctrip")

    def profile_for(self, provider: str) -> Path:
        from .channels import validate_provider
        return self.profile_root / validate_provider(provider, login=True)

    def lock_for(self, provider: str) -> Path:
        from .channels import validate_provider
        provider = validate_provider(provider, login=True)
        return self.data_dir / f"{provider.removesuffix('_h5')}.lock"

    @property
    def bark_path(self) -> Path:
        return self.data_dir / "bark.json"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "ctrip.lock"
