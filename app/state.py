from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import threading

@dataclass
class JobState:
    max_workers: int
    jobs: dict[str, dict] = field(default_factory=dict)
    queued_sources: set[tuple[str, str]] = field(default_factory=set)
    folder_candidates: dict[str, tuple[str, float]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
