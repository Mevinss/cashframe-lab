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

    @property
    def local(self):
        return self.root / 'local'

    @property
    def model_dir(self):
        return self.local / 'experiment'
