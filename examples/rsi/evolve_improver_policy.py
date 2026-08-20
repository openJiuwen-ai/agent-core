# coding: utf-8
"""Build and validate candidate Improver policies from feedback ledgers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openjiuwen.rsi.improver_evolution import (
    MetaValidationThresholds,
    analyze_candidate_feedback_ledgers,
    default_improver_policy,
    load_improver_policy,
    paired_meta_validate,
    propose_policy_candidates,
    write_improver_policy,
)


def main() -> int:
    args = _parse_args()
    if args.command == "propose":
        return _propose(args)
    if args.command == "validate":
        return _validate(args)
    raise ValueError(f"unsupported command: {args.command!r}")


def _propose(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_paths = [Path(path).expanduser().resolve() for path in args.ledger]
    for path in ledger_paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    parent = (
        load_improver_policy(Path(args.parent_policy).expanduser().resolve())
        if args.parent_policy
        else default_improver_policy()
    )
    analysis = analyze_candidate_feedback_ledgers(
        ledger_paths,
        min_support_cohorts=args.min_support_cohorts,
        high_value_gain_threshold=args.high_value_gain_threshold,
    )
    candidates = propose_policy_candidates(
        parent,
        analysis,
        min_support=args.min_support_cohorts,
    )

    analysis_path = output_dir / "feedback_analysis.yaml"
    _write_yaml(analysis_path, analysis)
    policy_dir = output_dir / "candidate_policies"
    policy_paths = [write_improver_policy(policy_dir / f"{policy.version_id}.yaml", policy) for policy in candidates]
    manifest = {
        "schema_version": 1,
        "record_type": "improver_policy_candidate_manifest",
        "status": "awaiting_meta_validation" if candidates else "no_supported_candidate",
        "parent_improver": {
            "version_id": parent.version_id,
            "policy_digest": parent.canonical_digest,
        },
        "training_ledger_digest": analysis["training_ledger_digest"],
        "feedback_analysis_path": str(analysis_path),
        "candidate_count": len(candidates),
        "candidates": [
            {
                "version_id": policy.version_id,
                "parent_version_id": policy.parent_version_id,
                "policy_digest": policy.canonical_digest,
                "policy_path": str(path),
            }
            for policy, path in zip(candidates, policy_paths, strict=True)
        ],
        "promotion": {
            "status": "inconclusive",
            "reason": "paired_unseen_live_meta_validation_required",
        },
    }
    manifest_path = output_dir / "candidate_manifest.yaml"
    _write_yaml(manifest_path, manifest)

    print(f"FEEDBACK_ANALYSIS={analysis_path}")
    print(f"IMPROVER_CANDIDATE_MANIFEST={manifest_path}")
    print(f"IMPROVER_CANDIDATE_COUNT={len(candidates)}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    baseline_path = Path(args.baseline_results).expanduser().resolve()
    candidate_path = Path(args.candidate_results).expanduser().resolve()
    baseline_results = _checkpoint_results(_load_document(baseline_path), source=baseline_path)
    candidate_results = _checkpoint_results(_load_document(candidate_path), source=candidate_path)
    thresholds = (
        MetaValidationThresholds(**_mapping_document(Path(args.thresholds).expanduser().resolve()))
        if args.thresholds
        else None
    )
    report = paired_meta_validate(
        baseline_results=baseline_results,
        candidate_results=candidate_results,
        mode=args.mode,
        thresholds=thresholds,
        meta_train_checkpoint_ids=args.meta_train_checkpoint_id,
    )
    output_path = Path(args.output).expanduser().resolve()
    _write_yaml(output_path, report)
    print(f"META_VALIDATION_REPORT={output_path}")
    print(f"VALIDATION_STATUS={report['validation']['status']}")
    print(f"PROMOTION_STATUS={report['promotion']['status']}")
    return 0


def _checkpoint_results(document: Any, *, source: Path) -> list[dict[str, Any]]:
    records = document
    if isinstance(document, dict):
        records = document.get("results", document.get("checkpoints"))
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"checkpoint results must be a list of mappings: {source}")
    return records


def _mapping_document(path: Path) -> dict[str, Any]:
    document = _load_document(path)
    if not isinstance(document, dict):
        raise ValueError(f"document must contain a mapping: {path}")
    return document


def _load_document(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)


def _write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn Candidate Feedback Ledger evidence into versioned Improver policy experiments."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    propose = subparsers.add_parser("propose", help="Analyze Meta-Train ledgers and build policy candidates.")
    propose.add_argument("--ledger", action="append", required=True, help="Ledger v1 YAML/JSON; repeat as needed.")
    propose.add_argument("--parent-policy", default="", help="Parent policy YAML; defaults to I0.")
    propose.add_argument("--output-dir", required=True)
    propose.add_argument("--min-support-cohorts", type=int, default=2)
    propose.add_argument("--high-value-gain-threshold", type=float, default=0.0)

    validate = subparsers.add_parser("validate", help="Run paired validation on unseen checkpoint results.")
    validate.add_argument("--baseline-results", required=True)
    validate.add_argument("--candidate-results", required=True)
    validate.add_argument("--mode", choices=("offline_rerank", "live_generation"), required=True)
    validate.add_argument("--thresholds", default="", help="Optional MetaValidationThresholds YAML/JSON mapping.")
    validate.add_argument("--meta-train-checkpoint-id", action="append", default=[])
    validate.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
