from dataclasses import dataclass
from pathlib import Path
LABELS, AGENT_IDS = ("A", "B", "C", "D", "E"), (0, 1, 2)
DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parent / "artifacts"

@dataclass(frozen=True)
class ExperimentConfig:
    train_size: int = 30; val_size: int = 10; test_size: int = 20
    seed: int = 42; concurrency: int = 3; max_api_calls: int = 650
    request_timeout: float = 90.0; max_retries: int = 2; backoff_base: float = 1.0
    offline_mock: bool = False; force_regenerate: bool = False
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT
    epochs: int = 200; patience: int = 25
    learning_rate: float = 1e-3; weight_decay: float = 1e-4
    query_dim: int = 96; history_dim: int = 96; agent_embedding_dim: int = 12
    hidden_dim: int = 48; dropout: float = 0.05
    @property
    def mode(self) -> str: return "mock" if self.offline_mock else "real"
    @property
    def mode_root(self) -> Path: return self.artifact_root / self.mode
