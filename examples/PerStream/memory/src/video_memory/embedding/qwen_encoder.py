from __future__ import annotations

import json
import re
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn


def question_stem(question: str) -> str:
    match = re.search(r"\nA\.", question)
    return question[: match.start()].strip() if match else question.strip()


def format_query_text(question: str, qa_type: str) -> str:
    return f"[TYPE] {qa_type}\n[QUERY] {question_stem(question)}"


def format_node_text(node_type: str, description: str) -> str:
    return f"[TYPE] {node_type}\n[MEMORY] {description.strip()}"


def last_token_pool(hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    positions = torch.arange(attention_mask.shape[1], device=attention_mask.device)
    positions = positions.unsqueeze(0).expand_as(attention_mask)
    last_positions = positions.masked_fill(attention_mask == 0, -1).max(dim=1).values
    if bool((last_positions < 0).any()):
        raise ValueError("Cannot pool an input containing only padding tokens.")
    batch_indices = torch.arange(hidden_state.shape[0], device=hidden_state.device)
    return hidden_state[batch_indices, last_positions]


class QwenTextEncoder(nn.Module):
    def __init__(
        self,
        model_path: str | Path,
        device: str = "cuda:0",
        max_length: int = 320,
        trainable: bool = False,
        projection_dim: int | None = None,
        adapter_path: str | Path | None = None,
        lora_r: int = 8,
        lora_alpha: int = 16,
        lora_dropout: float = 0.05,
    ) -> None:
        super().__init__()
        if not device.startswith("cuda:"):
            raise ValueError("The 4-bit pilot encoder currently requires a CUDA device such as cuda:0.")
        if adapter_path is not None and trainable:
            raise ValueError("Resuming adapter training is not implemented for the pilot trainer.")

        from transformers import AutoTokenizer, BitsAndBytesConfig
        from transformers.models.qwen2_5_vl.modeling_qwen2_5_vl import Qwen2_5_VLModel

        self.device = torch.device(device)
        self.max_length = max_length
        model_path = Path(model_path)
        device_index = self.device.index if self.device.index is not None else 0
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )

        torch.cuda.set_device(device_index)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        base_model = Qwen2_5_VLModel.from_pretrained(
            model_path,
            local_files_only=True,
            quantization_config=quantization,
            device_map={"": device_index},
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            attn_implementation="sdpa",
        )
        base_model.config.use_cache = False

        if trainable:
            from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training

            base_model = prepare_model_for_kbit_training(
                base_model,
                use_gradient_checkpointing=True,
                gradient_checkpointing_kwargs={"use_reentrant": False},
            )
            lora_config = LoraConfig(
                r=lora_r,
                lora_alpha=lora_alpha,
                lora_dropout=lora_dropout,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                bias="none",
                task_type=TaskType.FEATURE_EXTRACTION,
            )
            self.model = get_peft_model(base_model, lora_config)
        elif adapter_path is not None:
            from peft import PeftModel

            adapter_path = Path(adapter_path)
            self.model = PeftModel.from_pretrained(base_model, adapter_path, is_trainable=False)
        else:
            self.model = base_model

        projection_state: dict[str, torch.Tensor] | None = None
        if adapter_path is not None:
            projection_path = Path(adapter_path) / "projection.pt"
            if not projection_path.exists():
                raise FileNotFoundError(f"Missing trained projection: {projection_path}")
            projection_state = torch.load(projection_path, map_location="cpu", weights_only=True)
            projection_dim = int(projection_state["weight"].shape[0])

        self.projection: nn.Linear | None = None
        if projection_dim is not None:
            if projection_dim <= 0:
                raise ValueError("projection_dim must be positive")
            self.projection = nn.Linear(
                int(self.model.config.hidden_size),
                projection_dim,
                bias=False,
                device=self.device,
                dtype=torch.float32,
            )
            if projection_state is not None:
                self.projection.load_state_dict(projection_state)

        self.trainable_encoder = trainable
        self.train(mode=trainable)

    @property
    def embedding_dim(self) -> int:
        if self.projection is not None:
            return int(self.projection.out_features)
        return int(self.model.config.hidden_size)

    def forward(self, texts: list[str]) -> torch.Tensor:
        if not texts:
            return torch.empty((0, self.embedding_dim), dtype=torch.float32, device=self.device)
        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        output = self.model(**inputs, use_cache=False, return_dict=True)
        pooled = last_token_pool(output.last_hidden_state, inputs["attention_mask"]).float()
        if self.projection is not None:
            pooled = self.projection(pooled)
        return F.normalize(pooled, p=2, dim=-1)

    def encode(self, texts: list[str], batch_size: int = 1) -> torch.Tensor:
        if not texts:
            return torch.empty((0, self.embedding_dim), dtype=torch.float32)

        was_training = self.training
        self.eval()
        vectors: list[torch.Tensor] = []
        try:
            for start in range(0, len(texts), batch_size):
                with torch.inference_mode():
                    vectors.append(self(texts[start : start + batch_size]).cpu())
        finally:
            self.train(was_training)
        return torch.cat(vectors, dim=0)

    def trainable_parameter_counts(self) -> dict[str, int]:
        model_count = sum(parameter.numel() for parameter in self.model.parameters() if parameter.requires_grad)
        projection_count = 0
        if self.projection is not None:
            projection_count = sum(
                parameter.numel() for parameter in self.projection.parameters() if parameter.requires_grad
            )
        return {
            "model": model_count,
            "projection": projection_count,
            "total": model_count + projection_count,
        }

    def save_checkpoint(self, path: str | Path, metadata: dict[str, object]) -> None:
        if not self.trainable_encoder or self.projection is None:
            raise RuntimeError("Only a trainable encoder with a projection can be checkpointed.")
        path = Path(path)
        if path.exists() and any(path.iterdir()):
            raise FileExistsError(f"Refusing to overwrite an existing checkpoint: {path}")
        path.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(path, safe_serialization=True)
        torch.save(self.projection.state_dict(), path / "projection.pt")
        (path / "training_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def memory_stats(self) -> dict[str, float]:
        index = self.device.index if self.device.index is not None else 0
        return {
            "allocated_gib": torch.cuda.memory_allocated(index) / 1024**3,
            "reserved_gib": torch.cuda.memory_reserved(index) / 1024**3,
            "peak_allocated_gib": torch.cuda.max_memory_allocated(index) / 1024**3,
            "peak_reserved_gib": torch.cuda.max_memory_reserved(index) / 1024**3,
        }
