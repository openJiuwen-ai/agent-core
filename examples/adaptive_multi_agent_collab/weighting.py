from __future__ import annotations
import hashlib, json, math, re, time
from collections import Counter; from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path; from typing import Any, Mapping, Sequence
import torch; from torch import Tensor, nn; from torch.nn import functional as F
ANSWER_LABELS = ("A", "B", "C", "D", "E")
DEFAULT_ROLES = {0: "analytical solver", 1: "option eliminator", 2: "skeptical verifier"}
def _dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return value.to_dict() if hasattr(value, "to_dict") else vars(value)
def _agent_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    if not (match := re.search(r"\d+", str(value))):
        raise ValueError(f"Invalid agent identifier: {value!r}")
    return int(match.group())
class SignedHashEncoder:
    def __init__(self, dimension: int = 96, namespace: str = "openjiuwen-v24") -> None:
        if dimension <= 0:
            raise ValueError("Encoder dimension must be positive")
        self.dimension, self.namespace = dimension, namespace
    def encode(self, text: str) -> Tensor:
        words = re.findall(r"[a-z0-9]+|[^\w\s]", text.casefold())
        features = [f"u:{word}" for word in words]
        features += [f"b:{a}\x1f{b}" for a, b in zip(words, words[1:])]
        features.append(f"t:{text}")
        vector = torch.zeros(self.dimension)
        for feature in features:
            digest = hashlib.blake2b(f"{self.namespace}\x1e{feature}".encode(), digest_size=16).digest()
            vector[int.from_bytes(digest[:8], "little") % self.dimension] += 1.0 if digest[8] & 1 else -1.0
        norm = torch.linalg.vector_norm(vector)
        return (vector / norm if norm else vector).detach()
def query_text(value: Any) -> str:
    data = _dict(value)
    example = _dict(data.get("example", data))
    options = example.get("options", {})
    rendered = [f"{label}: {options[label]}" for label in ANSWER_LABELS if label in options]
    return "\n".join([str(example.get("question", "")), *rendered])
def _initial_turns(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw = data.get("initial_turns", data.get("initial_answers", {}))
    items = raw.items() if isinstance(raw, Mapping) else enumerate(raw)
    return {_agent_id(key if isinstance(raw, Mapping) else item.get("agent_id", key)): _dict(item) for key, item in items}
def trajectory_terminal_answers(value: Any, num_agents: int = 3) -> list[list[str]]:
    data, result, raw = _dict(value), [[] for _ in range(num_agents)], _dict(value).get("terminal_answers", {})
    for agent in range(num_agents):
        items = raw.get(agent, raw.get(str(agent), [])) if isinstance(raw, Mapping) else (raw[agent] if agent < len(raw) else [])
        result[agent] = [str(item).upper() for item in items if str(item).upper() in ANSWER_LABELS]
    for raw_item in data.get("conversations", []):
        item = _dict(raw_item)
        agent, answer = _agent_id(item.get("initiator_id", 0)), str(item.get("terminal_answer", "")).upper()
        if not result[agent] and answer in ANSWER_LABELS:
            result[agent].append(answer)
    return result
def perspective_history(value: Any, agent_id: int, roles: Mapping[int, str] | None = None) -> str:
    data, roles, lists = _dict(value), roles or DEFAULT_ROLES, trajectory_terminal_answers(value)
    initial = _initial_turns(data).get(agent_id, {})
    own = next((_dict(x) for x in data.get("conversations", []) if _agent_id(_dict(x).get("initiator_id", -1)) == agent_id), {})
    terminal, answer = (lists[agent_id][-1] if lists[agent_id] else None), initial.get("answer")
    recommended, revised = own.get("reviewer_recommended_answer"), own.get("revision_answer")
    payload = {
        "agent_id": agent_id, "role": roles.get(agent_id, f"agent {agent_id}"), "initial_answer": answer,
        "initial_justification": initial.get("justification", ""), "reviewer_id": own.get("reviewer_id"),
        "reviewer_status": own.get("reviewer_status"), "reviewer_recommended_answer": recommended,
        "reviewer_feedback": own.get("reviewer_feedback", ""), "reviewer_agreed": recommended == answer,
        "revision_occurred": bool(revised), "revised_answer": revised, "terminal_answer": terminal,
        "other_terminal_labels": {str(i): labels for i, labels in enumerate(lists) if i != agent_id},
        "disagreement_pattern": dict(sorted(Counter(y for labels in lists for y in labels).items())),
        "changed_answer": bool(terminal and answer and terminal != answer),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
def modal_set(answers: Sequence[str]) -> set[str]:
    normalized = [str(answer).upper() for answer in answers]
    if invalid := set(normalized) - set(ANSWER_LABELS):
        raise ValueError(f"Unsupported answer labels: {sorted(invalid)}")
    counts = Counter(normalized); return {answer for answer, count in counts.items() if count == max(counts.values())} if counts else set()
def support_components(answers: Sequence[str], candidates: Sequence[str] = ANSWER_LABELS) -> tuple[Tensor, Tensor, Tensor]:
    if not answers:
        zeros = torch.zeros(len(candidates))
        return zeros.clone(), zeros.clone(), zeros
    normalized, modes = [str(x).upper() for x in answers], modal_set(answers)
    rho = torch.tensor([normalized.count(label) / len(normalized) for label in candidates])
    mu = torch.tensor([1 / len(modes) if label in modes else 0 for label in candidates])
    return rho, mu, 0.5 * (rho + mu)
def terminal_support_tensor(terminal_answers: Sequence[Sequence[str]]) -> Tensor:
    return torch.stack([support_components(answers)[2] for answers in terminal_answers]).detach()
@dataclass(frozen=True)
class WeightingConfig:
    query_dim: int = 96; history_dim: int = 96; agent_embedding_dim: int = 12
    hidden_dim: int = 48; dropout: float = 0.0
    learning_rate: float = 1e-3; weight_decay: float = 1e-4
    epochs: int = 200; patience: int = 25; batch_size: int = 8; seed: int = 42
@dataclass(frozen=True)
class WeightingExample:
    example_id: str; query: Tensor; histories: Tensor
    support: Tensor; gold_index: int
class WeightingModel(nn.Module):
    def __init__(self, config: WeightingConfig | None = None, num_agents: int = 3) -> None:
        super().__init__()
        self.config, self.num_agents = config or WeightingConfig(), num_agents
        cfg = self.config
        self.agent_embeddings = nn.Embedding(num_agents, cfg.agent_embedding_dim)
        self.mlp = nn.Sequential(nn.Linear(cfg.query_dim + cfg.agent_embedding_dim + cfg.history_dim, cfg.hidden_dim),
                                 nn.GELU(), nn.Dropout(cfg.dropout), nn.Linear(cfg.hidden_dim, 1))
    def forward(self, query: Tensor, histories: Tensor) -> Tensor:
        squeeze = query.ndim == 1
        query, histories = (query[None], histories[None]) if squeeze else (query, histories)
        ids = torch.arange(self.num_agents, device=query.device)
        agents = self.agent_embeddings(ids)[None].expand(query.shape[0], -1, -1)
        queries = query[:, None].expand(-1, self.num_agents, -1)
        weights = torch.softmax(self.mlp(torch.cat((queries, agents, histories), -1)).squeeze(-1), -1)
        return weights[0] if squeeze else weights
    def candidate_scores(self, query: Tensor, histories: Tensor, support: Tensor) -> Tensor:
        return torch.sum(self(query, histories).unsqueeze(-1) * support.detach(), dim=-2)
@dataclass
class TrainingResult:
    device: str; best_epoch: int
    best_train_loss: float; best_validation_loss: float
    history: list[dict[str, float]]; training_seconds: float; checkpoint_path: str
def encode_weighting_features(value: Any, config: WeightingConfig | None = None) -> tuple[Tensor, Tensor, Tensor]:
    cfg, terminals = config or WeightingConfig(), trajectory_terminal_answers(value)
    query = SignedHashEncoder(cfg.query_dim, "query").encode(query_text(value))
    histories = torch.stack([SignedHashEncoder(cfg.history_dim, "history").encode(
        perspective_history(value, agent)) for agent in range(3)])
    return query, histories, terminal_support_tensor(terminals)
def prepare_weighting_examples(values: Sequence[Any], config: WeightingConfig | None = None) -> list[WeightingExample]:
    cfg, prepared = config or WeightingConfig(), []
    for value in values:
        data = _dict(value)
        example = _dict(data.get("example", data))
        gold = str(example.get("gold_label", example.get("gold_answer", ""))).upper()
        if gold not in ANSWER_LABELS:
            continue
        query, histories, support = encode_weighting_features(value, cfg)
        prepared.append(WeightingExample(str(example.get("example_id", example.get("id", ""))), query, histories,
                                          support, ANSWER_LABELS.index(gold)))
    return prepared
def _device() -> torch.device:
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")
def _loss(model: WeightingModel, examples: Sequence[WeightingExample], device: torch.device) -> Tensor:
    if not examples:
        raise ValueError("Weighting requires at least one completed trajectory")
    query = torch.stack([x.query for x in examples]).to(device)
    histories = torch.stack([x.histories for x in examples]).to(device)
    support = torch.stack([x.support for x in examples]).to(device).detach()
    gold = torch.tensor([x.gold_index for x in examples], device=device)
    return F.cross_entropy(model.candidate_scores(query, histories, support), gold)
def evaluate_weighting_loss(model: WeightingModel, examples: Sequence[WeightingExample], device: Any = None) -> float:
    target = torch.device(device) if device else next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        return float(_loss(model, examples, target).cpu())
def train_weighting_model(train: Sequence[WeightingExample], validation: Sequence[WeightingExample],
                          checkpoint_path: str | Path, config: WeightingConfig | None = None) -> TrainingResult:
    cfg, device, started = config or WeightingConfig(), _device(), time.perf_counter()
    if not train or not validation:
        raise ValueError("Weighting training and validation sets must both be non-empty")
    torch.manual_seed(cfg.seed)
    model, path = WeightingModel(cfg).to(device), Path(checkpoint_path)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay)
    best_loss, best_epoch, stale, history = math.inf, 0, 0, []
    path.parent.mkdir(parents=True, exist_ok=True)
    generator = torch.Generator().manual_seed(cfg.seed)
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        order = torch.randperm(len(train), generator=generator).tolist()
        for start in range(0, len(order), cfg.batch_size):
            optimizer.zero_grad(set_to_none=True)
            loss = _loss(model, [train[i] for i in order[start:start + cfg.batch_size]], device)
            loss.backward()
            optimizer.step()
        train_loss, val_loss = evaluate_weighting_loss(model, train, device), evaluate_weighting_loss(model, validation, device)
        history.append({"epoch": float(epoch), "train_loss": train_loss, "validation_loss": val_loss})
        if val_loss < best_loss - 1e-8:
            best_loss, best_epoch, stale = val_loss, epoch, 0
            torch.save({"state_dict": model.state_dict(), "config": asdict(cfg), "best_epoch": epoch}, path)
        else:
            stale += 1
            if stale >= cfg.patience:
                break
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True)["state_dict"])
    best_train = evaluate_weighting_loss(model, train, device)
    return TrainingResult(str(device), best_epoch, best_train, best_loss, history,
                          time.perf_counter() - started, str(path))
def load_weighting_checkpoint(path: str | Path, device: Any = None) -> tuple[WeightingModel, dict[str, Any]]:
    target, payload = torch.device(device) if device else _device(), torch.load(path, map_location=device or _device(), weights_only=True)
    model = WeightingModel(WeightingConfig(**payload["config"])).to(target)
    model.load_state_dict(payload["state_dict"])
    return model.eval(), payload
def infer_weights(model: WeightingModel, trajectory: Any, config: WeightingConfig | None = None) -> tuple[list[float], float]:
    started = time.perf_counter()
    query, histories, _ = encode_weighting_features(trajectory, config or model.config)
    device = next(model.parameters()).device
    with torch.no_grad():
        weights = model(query.to(device), histories.to(device)).cpu().tolist()
    return weights, time.perf_counter() - started
