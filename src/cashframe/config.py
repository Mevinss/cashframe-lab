from dataclasses import dataclass, field
from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    root: Path = ROOT
    api_key: str = field(default_factory=lambda: os.getenv('CASHFRAME_API_KEY', ''))
    max_upload_bytes: int = 100 * 1024 * 1024
    deadline_sec: float = 270
    max_duration_sec: float = 180
    retention_hours: int = 24
    max_worker_rss_mb: int = 2048
    min_free_disk_mb: int = 512
    max_stored_jobs: int = 100
    model_directory: Path | None = None

    @property
    def local(self):
        return self.root / 'local'

    @property
    def model_dir(self):
        if self.model_directory is None:return self.local / 'experiment'
        path=Path(self.model_directory)
        return path if path.is_absolute() else self.root/path
