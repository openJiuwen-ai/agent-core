"""Local campaign lifecycle, immutable archival, and independent pilot registration.

Archival never settles a receipt. Registration never activates policy or grants
generation authority. Shared compatibility evidence is distinct from pilot funds.
"""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
from ..infrastructure.storage import load_checkpoint, load_ledger, file_sha256

import copy
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

ROOT = PACKAGE_ROOT
CAMPAIGN_PROTOCOL = "independent_campaign_v1"
REGISTRY_PROTOCOL = "campaign_registry_v1"
ARCHIVE_PROTOCOL = "verified_campaign_archive_v3"
ARCHIVE_PROTOCOLS = {
    "verified_campaign_archive_v1", "verified_campaign_archive_v2", ARCHIVE_PROTOCOL
}


def _read(path: str | Path) -> dict[str, Any]:
    return load_ledger(path)


def _sha(path: str | Path) -> str:
    return file_sha256(path)

def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _root(root: Path | None = None) -> Path:
    value = (root or ROOT).resolve()
    if not value.is_relative_to(ROOT):
        raise ValueError("campaign operations must stay inside the permitted directory")
    return value


def _safe(path: str | Path, root: Path) -> Path:
    value = Path(os.path.abspath(path))
    if not value.is_relative_to(root) or value == root:
        raise ValueError("campaign artifact lies outside the permitted directory")
    for node in (value, *value.parents):
        if node == root:
            break
        if node.is_symlink():
            raise ValueError("campaign operations do not follow symlinks")
    return value


def _write(path: Path, value: Any) -> None:
    from .experiment import _write_json_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, value)


def _registry(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "runs/campaign_registry.json"
    value = (
        _read(path)
        if path.exists()
        else {"protocol": REGISTRY_PROTOCOL, "campaigns": {}}
    )
    if value.get("protocol") != REGISTRY_PROTOCOL or not isinstance(
        value.get("campaigns"), dict
    ):
        raise ValueError("corrupt campaign registry")
    return path, value


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "another process is executing this campaign or lifecycle operation"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def assert_campaign_open(directory: str | Path, *, root: Path | None = None) -> None:
    root = _root(root)
    value = Path(directory).resolve()
    if value.is_relative_to(root / "archive"):
        raise ValueError("archived campaign state cannot execute or resume")
    _, registry = _registry(root)
    for entry in registry["campaigns"].values():
        if value.is_relative_to(Path(entry["directory"])) and entry["lifecycle"] in {
            "closing",
            "closed-superseded",
        }:
            raise ValueError(
                "closed campaign is closed-superseded; old execution is disabled"
            )


def _stable_lock_path(directory: Path, root: Path) -> Path:
    """A removable run directory must never own the only admission lock."""
    identity = hashlib.sha256(str(directory).encode()).hexdigest()
    return root / "runs/.campaign_locks" / (identity + ".lock")


@contextmanager
def campaign_lock(directory: str | Path, *, root: Path | None = None) -> Iterator[None]:
    """Serialize new execution with retirement without recreating a closed path."""
    root = _root(root)
    directory = _safe(directory, root)
    assert_campaign_open(directory, root=root)
    with _file_lock(_stable_lock_path(directory, root)):
        assert_campaign_open(directory, root=root)
        directory.mkdir(parents=True, exist_ok=True)
        with _file_lock(directory / ".campaign.lock"):
            assert_campaign_open(directory, root=root)
            yield


@contextmanager
def _retirement_lock(directory: Path, root: Path) -> Iterator[None]:
    """Also exclude older executors that still hold the historical local lock."""
    with _file_lock(_stable_lock_path(directory, root)), ExitStack() as stack:
        if directory.exists():
            stack.enter_context(_file_lock(directory / ".campaign.lock"))
        yield


def _move_verified(source: Path, target: Path, sha256: str) -> None:
    """Copy durably, verify, then unlink; an interrupted verified copy is resumable."""
    if target.exists():
        if not target.is_file() or _sha(target) != sha256:
            raise ValueError("archive destination collision or changed archived bytes")
    else:
        if not source.is_file() or _sha(source) != sha256:
            raise ValueError("archive source is missing or changed")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".partial", delete=False
        ) as out:
            temporary = Path(out.name)
            try:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, out)
                out.flush()
                os.fsync(out.fileno())
                if _sha(temporary) != sha256:
                    raise ValueError("archive copy verification failed")
                shutil.copystat(source, temporary, follow_symlinks=False)
                os.link(temporary, target)  # Atomic no-overwrite publication.
            finally:
                temporary.unlink(missing_ok=True)
    if _sha(target) != sha256:
        raise ValueError("archive destination verification failed")
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if source.exists():
        if _sha(source) != sha256:
            raise ValueError("source changed after archival copy; refusing removal")
        source.unlink()
        descriptor = os.open(source.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_archive(
    destination: str | Path, *, root: Path | None = None
) -> dict[str, Any]:
    root = _root(root)
    destination = _safe(destination, root)
    manifest = _read(destination / "ARCHIVE_MANIFEST.json")
    if manifest.get("protocol") not in ARCHIVE_PROTOCOLS or manifest.get(
        "fingerprint"
    ) != _digest(
        {key: value for key, value in manifest.items() if key != "fingerprint"}
    ):
        raise ValueError("archive manifest fingerprint changed")
    for row in manifest["artifacts"]:
        target = _safe(row["archive_path"], root)
        if not target.is_relative_to(destination) or _sha(target) != row["sha256"]:
            raise ValueError("archived artifact hash changed")
    return {
        "verified_files": len(manifest["artifacts"]),
        "fingerprint": manifest["fingerprint"],
    }


def _archive_outcome(campaign: Path) -> dict[str, Any]:
    """Read historical facts without restoring an incompatible training model."""
    ledger_path = campaign / "expenditure_ledger.json"
    ledger = _read(ledger_path)
    entries = ledger.get("entries")
    accounting_complete = isinstance(entries, list) and all(
        row.get("charged") is not None
        and all(
            row.get("usage", {}).get(name) is not None
            for name in ("input_tokens", "output_tokens", "reference_cost")
        )
        for row in entries
    )
    result_path, pilot_path = campaign / "result.json", campaign / "pilot_record.json"
    result = _read(result_path) if result_path.is_file() else {}
    pilot = _read(pilot_path) if pilot_path.is_file() else {}
    planned = pilot.get("planned_workflow_attempts")
    execution_complete = bool(
        result.get("stage_complete") and pilot.get("stage_complete")
        and isinstance(planned, int) and planned > 0
        and pilot.get("completed_workflows") == planned
    )
    evidence = {str(ledger_path): _sha(ledger_path)}
    for path in (result_path, pilot_path):
        if path.is_file():
            evidence[str(path)] = _sha(path)
    if execution_complete:
        from ..infrastructure.persistence import read_journal

        checkpoint = campaign / "checkpoint.pt"
        payload = load_checkpoint(checkpoint)
        state = payload.get("trainer_state", {})
        observations = pilot.get("observations", [])
        if (
            payload.get("request_boundary_pending") is not False
            or state.get("pilot_complete") is not True
            or state.get("phase") != "pilot_complete"
            or result.get("pilot_complete") is not True
            or result.get("operational_halt") is not None
            or not isinstance(planned, int)
            or pilot.get("attempted_workflows") != planned
            or pilot.get("completed_workflows") != planned
            or len(observations) != planned
            or any(not row.get("successful") for row in observations)
            or pilot.get("operational_halt") is not None
            or _digest(payload.get("hook_state", {}).get("expenditure_ledger"))
            != _digest(ledger)
        ):
            raise ValueError("completed archival claim conflicts with checkpoint or workflow evidence")
        progress = read_journal(campaign)
        if (
            progress is None
            or progress["boundary"] is None
            or progress["boundary"]["checkpoint_sha256"] != _sha(checkpoint)
            or _digest(progress["state"]) != _digest(ledger)
        ):
            raise ValueError("completed archival claim lacks a matching safe durable publication")
        evidence[str(checkpoint)] = _sha(checkpoint)
        journal = campaign / "accounting_progress.jsonl"
        evidence[str(journal)] = _sha(journal)
        if pilot.get("accounting_complete_workflows") != planned:
            accounting_complete = False
    return {
        "execution_complete": execution_complete,
        "accounting_complete": accounting_complete,
        "accounting_status": "accounting-complete" if accounting_complete else "accounting-unresolved",
        "outcome_evidence": evidence,
    }


def archive_campaign(
    campaign: str | Path,
    destination: str | Path,
    artifacts: Mapping[str, str],
    shared: list[str],
    authorization: Mapping[str, Any],
    *,
    root: Path | None = None,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    root = _root(root)
    campaign, destination = _safe(campaign, root), _safe(destination, root)
    if not campaign.is_relative_to(root / "runs") or not destination.is_relative_to(
        root / "archive"
    ):
        raise ValueError(
            "archive requires a run directory and a distinct archive directory"
        )
    if (
        not authorization
        or not authorization.get("source")
        or not authorization.get("scope")
    ):
        raise ValueError(
            "offline archive needs its actual authorization source and scope"
        )
    with (
        _file_lock(root / "runs/.registry.lock"),
        _retirement_lock(campaign, root),
    ):
        registry_path, registry = _registry(root)
        matches = [
            key
            for key, value in registry["campaigns"].items()
            if value["directory"] == str(campaign)
        ]
        if len(matches) > 1:
            raise ValueError("multiple registered identities name this campaign")
        selected_id = matches[0] if matches else "Pilot_1"
        if campaign_id is not None and campaign_id != selected_id:
            raise ValueError("archive campaign identity differs from its registration")
        if destination != root / "archive" / selected_id:
            raise ValueError("archive destination differs from the campaign identity")
        prior = registry["campaigns"].get(selected_id)
        if selected_id != "Pilot_1" and prior is None:
            raise ValueError(
                "successor archival requires its existing campaign registration"
            )
        if prior is not None and prior["lifecycle"] == "registered":
            source_manifest = _safe(prior["manifest_path"], root)
            source_specification = _read(source_manifest)
            marker = source_specification.get("independent_campaign", {})
            if (
                marker.get("campaign_id") != selected_id
                or source_specification.get("campaign_directory") != str(campaign)
                or _sha(source_manifest) != prior["manifest_sha256"]
                or _digest(source_specification) != prior["specification_digest"]
            ):
                raise ValueError(
                    "registered archival identity or original manifest changed"
                )
            ledger_path = campaign / "expenditure_ledger.json"
            ledger = _read(ledger_path)
            if ledger.get("campaign_binding") != campaign_binding(
                source_manifest, source_specification
            ):
                raise ValueError(
                    "archival ledger belongs to a different registered campaign"
                )
            required = {source_manifest, ledger_path, campaign / "checkpoint.pt"}
            if (campaign / "accounting_progress.jsonl").exists():
                required.add(campaign / "accounting_progress.jsonl")
            if not required.issubset({_safe(path, root) for path in artifacts}):
                raise ValueError(
                    "registered archival selection omits required campaign state"
                )
        manifest_path = destination / "ARCHIVE_MANIFEST.json"
        if manifest_path.exists():
            manifest = _read(manifest_path)
            if (
                manifest.get("campaign_directory") != str(campaign)
                or manifest.get("campaign_id", "Pilot_1") != selected_id
                or manifest.get("retained_shared_or_ambiguous") != sorted(shared)
                or manifest.get("authorization") != dict(authorization)
                or {row["original_path"]: row["role"] for row in manifest["artifacts"]}
                != {str(_safe(path, root)): role for path, role in artifacts.items()}
                or manifest["fingerprint"]
                != _digest({k: v for k, v in manifest.items() if k != "fingerprint"})
            ):
                raise ValueError("archive resumption differs from the frozen migration")
        else:
            if destination.exists():
                raise ValueError(
                    "archive destination collision; existing contents cannot be overwritten"
                )
            rows = []
            for name, role in sorted(artifacts.items()):
                source = _safe(name, root)
                if not source.is_file() or source.is_relative_to(destination):
                    raise ValueError(
                        "archive selection requires existing regular source artifacts"
                    )
                rows.append(
                    {
                        "original_path": str(source),
                        "archive_path": str(destination / source.relative_to(root)),
                        "sha256": _sha(source),
                        "bytes": source.stat().st_size,
                        "role": role,
                    }
                )
            manifest = {
                "protocol": ARCHIVE_PROTOCOL,
                "campaign_id": selected_id,
                "campaign_directory": str(campaign),
                "archive_directory": str(destination),
                "authorization": dict(authorization),
                "artifacts": rows,
                "retained_shared_or_ambiguous": sorted(shared),
                "outcome": _archive_outcome(campaign),
            }
            manifest["fingerprint"] = _digest(manifest)
            _write(manifest_path, manifest)
        closure = {
            **(prior or {}),
            "campaign_id": selected_id,
            "directory": str(campaign),
            "archive_directory": str(destination),
            "archive_manifest_fingerprint": manifest["fingerprint"],
            "lifecycle": "closing",
            **manifest.get("outcome", {
                "execution_complete": False,
                "accounting_complete": False,
                **({"accounting_status": "accounting-unresolved"}
                   if manifest["protocol"] == "verified_campaign_archive_v2" else {}),
            }),
        }
        if (
            prior is not None
            and prior["lifecycle"] in {"closing", "closed-superseded"}
            and any(prior.get(k) != v for k, v in closure.items() if k != "lifecycle")
        ):
            raise ValueError("campaign closure registration changed")
        if prior is None or prior["lifecycle"] != "closed-superseded":
            registry["campaigns"][selected_id] = closure
            _write(registry_path, registry)
        # Closure is durable before moving any receipt, checkpoint or approval.
        for row in manifest["artifacts"]:
            _move_verified(
                _safe(row["original_path"], root),
                _safe(row["archive_path"], root),
                row["sha256"],
            )
        verified = verify_archive(destination, root=root)
        closure["lifecycle"] = "closed-superseded"
        registry["campaigns"][selected_id] = closure
        _write(
            destination / "lifecycle.json",
            {
                **closure,
                "verification": verified,
                "historical_receipts_modified": False,
                "unknowns_settled": False,
            },
        )
        _write(registry_path, registry)
        return {**closure, **verified, "manifest": str(manifest_path)}


def remove_closed_campaign_directory(
    campaign: str | Path,
    *,
    authorization: Mapping[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    """Archive unique closure metadata, then remove only a verified retired tree.

    The immutable original archive is never modified. The central registration
    and stable lock survive removal, so stale launch paths cannot recreate it.
    """
    root = _root(root)
    campaign = _safe(campaign, root)
    if not authorization.get("source") or not authorization.get("scope"):
        raise ValueError("closed-directory removal needs explicit offline authorization")
    with _file_lock(root / "runs/.registry.lock"), _retirement_lock(campaign, root):
        _, registry = _registry(root)
        matches = [item for item in registry["campaigns"].values() if item["directory"] == str(campaign)]
        if len(matches) != 1 or matches[0]["lifecycle"] != "closed-superseded":
            raise ValueError("directory removal requires a centrally closed campaign")
        closure = matches[0]
        destination = _safe(closure["archive_directory"], root)
        original = verify_archive(destination, root=root)
        if original["fingerprint"] != closure["archive_manifest_fingerprint"]:
            raise ValueError("central closure and immutable archive identity differ")
        path = destination / "SUPPLEMENTAL_ARCHIVE_MANIFEST.json"
        if path.exists():
            supplement = _read(path)
            if (
                supplement.get("authorization") != dict(authorization)
                or supplement.get("campaign_directory") != str(campaign)
                or supplement.get("original_archive_fingerprint") != original["fingerprint"]
                or supplement.get("fingerprint") != _digest({k: v for k, v in supplement.items() if k != "fingerprint"})
            ):
                raise ValueError("closed-directory restart differs from frozen cleanup")
        else:
            rows = []
            if campaign.exists():
                for item in sorted(campaign.rglob("*")):
                    _safe(item, root)
                    if item.is_dir():
                        continue
                    if not item.is_file():
                        raise ValueError("unrecognized retired artifact; preserve for review")
                    if item.name == ".campaign.lock" and item.stat().st_size == 0:
                        continue
                    rows.append({
                        "original_path": str(item),
                        "archive_path": str(destination / "supplemental" / item.relative_to(campaign)),
                        "sha256": _sha(item), "bytes": item.stat().st_size,
                        "role": "retired-directory-closure-or-audit-metadata",
                    })
            supplement = {
                "protocol": "verified_retired_directory_removal_v1",
                "campaign_id": closure["campaign_id"], "campaign_directory": str(campaign),
                "original_archive_fingerprint": original["fingerprint"],
                "authorization": dict(authorization), "artifacts": rows,
            }
            supplement["fingerprint"] = _digest(supplement)
            _write(path, supplement)
        for row in supplement["artifacts"]:
            source, target = _safe(row["original_path"], root), _safe(row["archive_path"], root)
            if not source.is_relative_to(campaign) or not target.is_relative_to(destination / "supplemental"):
                raise ValueError("supplemental archive path differs from the retired campaign")
            _move_verified(source, target, row["sha256"])
        if campaign.exists():
            unexpected = [item for item in campaign.rglob("*") if item.is_file() and item.name != ".campaign.lock"]
            if unexpected:
                raise ValueError("new unregistered retired artifacts appeared; refusing deletion")
        result = {"campaign_id": closure["campaign_id"], "supplement": str(path),
                  "supplemental_files": len(supplement["artifacts"]), "original_archive": original}
    # Close the local legacy descriptor before deleting its path. The durable
    # central closure excludes launchers while reacquiring the stable gate.
    with _file_lock(root / "runs/.registry.lock"), _file_lock(_stable_lock_path(campaign, root)):
        if campaign.exists():
            local_lock = campaign / ".campaign.lock"
            if local_lock.exists():
                if local_lock.stat().st_size:
                    raise ValueError("unarchived local lock metadata appeared")
                local_lock.unlink()
            for item in sorted(campaign.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                _safe(item, root)
                if item.is_dir():
                    item.rmdir()
                else:
                    raise ValueError("unexpected retired file remains; preserve for review")
            campaign.rmdir()
    return {**result, "former_directory_removed": not campaign.exists()}


def validate_campaign(
    specification: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    marker = specification.get("independent_campaign", {})
    if marker.get("protocol") != CAMPAIGN_PROTOCOL:
        raise ValueError("missing independent campaign registration")
    registry_path = _safe(marker["registry_path"], ROOT)
    if registry_path != ROOT / "runs/campaign_registry.json":
        raise ValueError("campaign must use the canonical non-resetting registry")
    registry = _read(registry_path)
    entry = registry["campaigns"].get(marker.get("campaign_id"))
    campaign = Path(specification["campaign_directory"])
    campaign = (campaign if campaign.is_absolute() else directory / campaign).resolve()
    assert_campaign_open(campaign)
    if (
        entry is None
        or entry.get("lifecycle") != "registered"
        or entry.get("directory") != str(campaign)
        or entry.get("specification_digest") != _digest(dict(specification))
        or _sha(entry["manifest_path"]) != entry["manifest_sha256"]
    ):
        raise ValueError(
            "campaign is not registered with this exact independent manifest"
        )
    return copy.deepcopy(entry)


def campaign_binding(source: Path, specification: Mapping[str, Any]) -> dict[str, Any]:
    marker = specification.get("independent_campaign")
    if not marker:
        return {}
    return {
        "protocol": CAMPAIGN_PROTOCOL,
        "campaign_id": marker["campaign_id"],
        "directory": str(Path(specification["campaign_directory"]).resolve()),
        "registry_path": marker["registry_path"],
        "manifest_path": str(source.resolve()),
    }


def assert_registered_dispatch(ledger: Any) -> None:
    from ..config import ResourceConfig

    binding = ledger.campaign_binding
    assert_campaign_open(binding["directory"])
    source = Path(binding["manifest_path"])
    assert_campaign_open(source.parent)
    specification = _read(source)
    entry = validate_campaign(specification, source.parent)
    if binding != campaign_binding(
        source, specification
    ) or ledger.limits != ResourceConfig(**entry["limits"]):
        raise ValueError("ledger campaign identity or registered allowances changed")
    if ledger.failure_policy is None:
        raise ValueError("independent campaign failure-policy activation is pending")
    if ledger.stage == "compatibility":
        raise ValueError(
            "this pilot does not own the separate cumulative compatibility allowance"
        )


def shared_compatibility_evidence(
    specification: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    marker = specification["independent_campaign"]
    source = Path(marker["shared_evidence_path"])
    source = source if source.is_absolute() else directory / source
    if marker.get("shared_evidence_sha256") != _sha(source):
        raise ValueError(
            "shared compatibility index differs from the registered evidence"
        )
    evidence = _read(source)
    if evidence.get("protocol") != "shared_compatibility_evidence_v1":
        raise ValueError("invalid shared compatibility evidence")
    for ref in evidence["artifacts"].values():
        if _sha(ref["path"]) != ref["sha256"]:
            raise ValueError("shared compatibility artifact changed")
    from .preflight import gateway_fingerprint_fields

    actual = {
        key: {
            name: (
                gateway_fingerprint_fields(backend[name])
                if name == "gateway"
                else backend[name]
            )
            for name in (
                "model_name",
                "revision",
                "decoding",
                "supports_seed",
                "gateway",
                "resources",
                "role_output",
            )
        }
        for key, backend in specification["backends"].items()
    }
    if _digest(actual) != evidence["request_configuration_fingerprint"]:
        raise ValueError(
            "request behavior differs from the compatibility evidence scope"
        )
    historical = _read(evidence["artifacts"]["initial_compatibility"]["path"])
    roles = _read(evidence["artifacts"]["role_contract"]["path"])
    if (
        historical.get("status") != "complete"
        or historical.get("assessment", {}).get("passed") is not True
        or roles.get("stage_complete") is not True
        or len(roles.get("probes", [])) != 6
        or any(
            not item.get("valid_output") or not item.get("native_evidence_valid")
            for item in roles["probes"]
        )
    ):
        raise ValueError(
            "shared compatibility reports do not establish their declared scope"
        )
    return {
        **copy.deepcopy(evidence),
        "failure_policy_live_validated": False,
        "pilot_authorized": False,
        "historical_compatibility_report": historical,
    }


def preserve_shared_evidence(
    source_manifest: str | Path, destination: str | Path
) -> dict[str, Any]:
    """Copy only reusable receipt/plan files, retaining their bytes and scope."""
    from .policy_activation import _absolute_paths
    from .preflight import gateway_fingerprint_fields

    source, destination = _safe(source_manifest, ROOT), _safe(destination, ROOT)
    specification = _absolute_paths(_read(source), source.parent)
    campaign = Path(specification["campaign_directory"])
    registry = _read(campaign / "active_condition.json")
    current = Path(registry["state_directory"])
    history = _read(campaign / "condition_registry_history.json")["conditions"]
    prior = Path(history[0]["state_directory"])
    paths = {
        "initial_compatibility": campaign / "compatibility_report.json",
        "role_contract": current / "recheck_report.json",
        "role_plan": current / "probe_plan.json",
        "earlier_recheck": prior / "recheck_report.json",
        "earlier_plan": prior / "probe_plan.json",
    }
    destination.mkdir(parents=True, exist_ok=True)
    references = {}
    for key, original in paths.items():
        target = destination / (key + ".json")
        digest = _sha(original)
        if target.exists():
            if _sha(target) != digest:
                raise ValueError("shared evidence collision")
        else:
            from ..infrastructure.persistence import copy_exact

            copy_exact(original, target)
        references[key] = {
            "path": str(target),
            "sha256": digest,
            "original_path": str(original),
        }
    request_configuration = {
        key: {
            name: (
                gateway_fingerprint_fields(backend[name])
                if name == "gateway"
                else backend[name]
            )
            for name in (
                "model_name",
                "revision",
                "decoding",
                "supports_seed",
                "gateway",
                "resources",
                "role_output",
            )
        }
        for key, backend in specification["backends"].items()
    }
    evidence = {
        "protocol": "shared_compatibility_evidence_v1",
        "artifacts": references,
        "request_configuration_fingerprint": _digest(request_configuration),
        "response_identity_evidence": registry["response_identity_evidence"],
        "scope": "original backend/counter/billing/TextGrad observations, passed tie/revision probes, and six role-contract probes; no bounded-policy or multi-round live validation",
        "source_conditions": [item["condition_fingerprint"] for item in history],
        "failure_policy_live_validated": False,
        "semantic_limitation": "Recorded Gemini taxonomy objection remains a model error.",
    }
    path = destination / "index.json"
    if path.exists() and _read(path) != evidence:
        raise ValueError("shared compatibility index cannot be overwritten")
    if not path.exists():
        _write(path, evidence)
    return evidence


def initialize_pilot(
    source_manifest: str | Path,
    destination: str | Path,
    *,
    shared_evidence_path: str | Path,
    specification_sha256: str,
    specification_attachment: str | Path | None = None,
    authorization: Mapping[str, Any],
    builder: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Register one fresh USD5 pilot offline; duplicate invocation never resets it."""
    from .experiment import (
        build_experiment,
        prepare_experiment,
        planning_hooks,
        wire_durable_journal,
    )
    from ..infrastructure.failure_policy import proposed_failure_policy
    from .policy_activation import (
        _absolute_paths,
        _inert_model,
        build_independent_policy_plan,
    )
    from .preflight import stage_authorization_template
    from .projection import pilot_paid_projection
    from ..infrastructure.persistence import copy_exact
    from ..learning.training import parameter_fingerprints
    from ..providers.identity import with_response_identity_evidence

    destination = _safe(destination, ROOT)
    source = _safe(source_manifest, ROOT)
    campaign_id = destination.name
    if (
        campaign_id not in {"Pilot_2", "Pilot_3", "Pilot_4", "Pilot_5"}
        or destination != ROOT / "runs" / campaign_id
        or not authorization.get("source")
        or not authorization.get("scope")
    ):
        raise ValueError(
            "fresh pilot requires its explicitly supported directory and offline initialization approval"
        )
    if len(specification_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in specification_sha256
    ):
        raise ValueError("the supplied mathematical source requires an exact SHA256")
    if specification_attachment is not None:
        attachment = Path(specification_attachment).resolve()
        if not attachment.is_file() or _sha(attachment) != specification_sha256:
            raise ValueError(
                "explicit mathematical attachment does not match its declared SHA256"
            )
    else:
        attachment = None
    snapshot_required = campaign_id in {"Pilot_4", "Pilot_5"}
    if snapshot_required and attachment is None:
        raise ValueError(f"{campaign_id} requires its exact supplied mathematical source snapshot")
    # A missing retired directory is not a new campaign. Check before creating
    # either a directory or the old local lock, and repeat under the stable gate.
    assert_campaign_open(destination)
    with (
        _file_lock(ROOT / "runs/.registry.lock"),
        campaign_lock(destination),
    ):
        registry_path, registry = _registry(ROOT)
        old = registry["campaigns"].get(campaign_id)
        if old is not None and old.get("lifecycle") == "registered":
            registered_specification = _read(old["manifest_path"])
            validate_campaign(registered_specification, destination)
            if old.get("initialization_authorization") != dict(authorization):
                raise ValueError(
                    "registered initialization cannot be reauthorized or replaced"
                )
            if registered_specification["specification_sha256"] != specification_sha256:
                raise ValueError(
                    "registered mathematical condition cannot be replaced by initialization"
                )
            frozen = old.get("initialization_inputs", {})
            if frozen and (
                frozen.get("mathematical_attachment")
                != (None if attachment is None else str(attachment))
                or frozen["source_manifest_sha256"] != _sha(source)
                or frozen["shared_evidence_sha256"] != _sha(shared_evidence_path)
            ):
                raise ValueError(
                    "registered initialization source/evidence cannot be replaced"
                )
            build_independent_policy_plan(old["manifest_path"], destination)
            return _read(destination / "initialization.json")
        if old is not None and old.get("lifecycle") not in {
            "initializing",
            "registered",
        }:
            raise ValueError(
                "closed campaign cannot be initialized or granted another allowance"
            )
        if old is None and any(
            path.name != ".campaign.lock" for path in destination.iterdir()
        ):
            raise ValueError(
                "unregistered existing campaign contents cannot be reset or imported"
            )
        specification = _absolute_paths(_read(source), source.parent)
        specification.pop("revised_condition", None)
        specification.pop("policy_transition", None)
        specification.pop("independent_campaign", None)
        specification.pop("condition_change", None)
        specification.pop("role_recheck", None)
        specification["campaign_directory"] = str(destination)
        specification["independent_campaign"] = {
            "protocol": CAMPAIGN_PROTOCOL,
            "campaign_id": campaign_id,
            "registry_path": str(registry_path),
            "policy_plan_path": str(destination / "policy_plan.json"),
            "shared_evidence_path": str(_safe(shared_evidence_path, ROOT)),
            "shared_evidence_sha256": _sha(shared_evidence_path),
        }
        specification["config"]["failure_policy"] = asdict(proposed_failure_policy())
        specification["specification_sha256"] = specification_sha256
        if attachment is not None:
            specification["specification_attachment"] = str(
                destination / "mathematical_specification.tex"
                if snapshot_required else attachment
            )
        specification["approvals"] = {}
        specification["specification_amendments"] = [
            item
            for item in specification.get("specification_amendments", [])
            if item.get("scope")
            != "campaign separation and offline initialization only"
        ] + [
            {
                "source": "current explicit user instruction",
                "scope": "campaign separation and offline initialization only",
                "instruction": f"Proposed independent USD5 {campaign_id}, count8/USD0.50 uncertainty and backend streak5; no historical adoption, policy activation or generation authorization.",
            }
        ]
        evidence = shared_compatibility_evidence(specification, destination)
        initial_ref = evidence["artifacts"]["initial_compatibility"]
        for backend in (
            specification.get("attestations", {}).get("backends", {}).values()
        ):
            for item in backend.values():
                if (
                    isinstance(item, dict)
                    and item.get("report_sha256") == initial_ref["sha256"]
                ):
                    item["evidence"] = initial_ref["path"]
        compatibility = specification.setdefault("attestations", {}).setdefault(
            "compatibility", {}
        )
        compatibility.update(
            state="validated",
            report_sha256=initial_ref["sha256"],
            evidence=initial_ref["path"],
        )
        forecast = pilot_paid_projection(
            specification,
            compatibility_report=evidence["historical_compatibility_report"],
        )
        ordinary = forecast["scenarios"][0]
        specification["spending_plan"]["pilot"] = {
            "estimated_cost_usd": ordinary["total_paid_tariff_usd"],
            "headroom_usd": ordinary["proposed_headroom_usd"],
            "reviewed": False,
            "basis": "Unchanged eight-question/three-session ordinary and pessimistic workload assumptions; new independent USD5 ceiling. Forecast and execution approval pending.",
            "projection_file": str(destination / "pilot_forecast.json"),
        }
        inputs = {
            "campaign_id": campaign_id,
            "directory": str(destination),
            "lifecycle": "initializing",
            "initialization_authorization": dict(authorization),
            "initialization_inputs": {
                "source_manifest_sha256": _sha(source),
                "mathematical_specification_sha256": specification_sha256,
                "mathematical_attachment": (
                    None if attachment is None else str(attachment)
                ),
                "shared_evidence_sha256": _sha(shared_evidence_path),
                "proposed_specification_digest": _digest(specification),
            },
        }
        if old is not None and any(
            old.get(key) != value for key, value in inputs.items()
        ):
            raise ValueError(
                "interrupted initialization inputs or campaign identity changed"
            )
        if old is None:
            registry["campaigns"][campaign_id] = inputs
            _write(registry_path, registry)
        if snapshot_required:
            assert attachment is not None
            snapshot = destination / "mathematical_specification.tex"
            if snapshot.exists():
                if _sha(snapshot) != specification_sha256:
                    raise ValueError("registered mathematical snapshot cannot be replaced")
            else:
                copy_exact(attachment, snapshot)
        manifest = destination / "manifest.json"
        if manifest.exists():
            if _read(manifest) != specification:
                raise ValueError("interrupted campaign initialization inputs changed")
        else:
            _write(manifest, specification)
        prepared = prepare_experiment(manifest)
        entry = {
            **inputs,
            "manifest_path": str(manifest),
            "manifest_sha256": _sha(manifest),
            "specification_digest": _digest(specification),
            "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
            "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
            "limits": asdict(prepared.config.resources),
            "policy": asdict(prepared.config.failure_policy),
            "initialization_authorization": dict(authorization),
        }
        if old is not None and "manifest_path" in old and old != entry:
            raise ValueError(
                "interrupted campaign registration changed; refusing a fresh allowance"
            )
        registry["campaigns"][campaign_id] = entry
        _write(registry_path, registry)
        prepared = with_response_identity_evidence(
            prepared, evidence["response_identity_evidence"]
        )
        import numpy as np

        np.random.seed(prepared.config.seed)
        trainer, hooks = (
            builder(prepared)
            if builder
            else build_experiment(prepared, model_factory=_inert_model)
        )
        if (
            trainer.state.phase != "unstarted"
            or any(trainer.state.updates.values())
            or trainer.alpha_optimizer.state
            or trainer.weighting_optimizer.state
            or hooks.expenditure_ledger.entries
            or hooks.stage_authorizations
            or hooks.pilot_observations
            or hooks.evaluation_records
            or hooks.evaluation_cache
            or hooks.safety_record is not None
            or hooks.termination_record is not None
        ):
            raise ValueError(
                "fresh pilot initialization imported execution or training state"
            )
        ledger = hooks.expenditure_ledger
        ledger.stage = "pilot"
        ledger.campaign_binding = campaign_binding(manifest, specification)
        hooks.authorization_required = True
        trainer.hooks, trainer.checkpoint_path = hooks, destination / "checkpoint.pt"
        if trainer.checkpoint_path.exists():
            # Only initialization crash recovery: validate the saved zero boundary
            # without restoring its RNG, parameters, optimizer or historical hooks.
            import torch

            payload = load_checkpoint(trainer.checkpoint_path)
            from ..learning.training import CheckpointManager

            if (
                payload.get("schema_version") != CheckpointManager.schema_version
                or payload.get("request_boundary_pending") is not False
            ):
                raise ValueError(
                    "existing initialization uses an incompatible or unsafe checkpoint"
                )
            expected_payload = trainer.checkpoint_payload()
            saved = payload.get("hook_state", {}).get("expenditure_ledger")
            if (
                saved != ledger.state_dict()
                or payload["trainer_state"] != expected_payload["trainer_state"]
            ):
                raise ValueError(
                    "existing checkpoint is not this untouched initialization"
                )
            for name in ("alpha", "weighting"):
                expected = getattr(trainer, name).state_dict()
                if set(expected) != set(payload[name]) or any(
                    not torch.equal(v.cpu(), payload[name][k])
                    for k, v in expected.items()
                ):
                    raise ValueError("existing initialized parameters differ")
            if (
                payload.get("gamma") != trainer.gamma.to_dict()
                or payload.get("alpha_optimizer", {}).get("state")
                or payload.get("weighting_optimizer", {}).get("state")
                or payload.get("best_system") is not None
                or payload.get("config") != trainer.config.to_dict()
                or payload.get("hook_state") != hooks.state_dict()
            ):
                raise ValueError(
                    "existing initialized checkpoint imported incompatible protocol or training state"
                )
        # Only this locked, registered, zero-state initialization can complete
        # an interrupted publication. No executed workflow is rebound here.
        wire_durable_journal(
            trainer, hooks, destination, allow_empty_initialization=True
        )
        try:
            if not trainer.checkpoint_path.exists():
                trainer._checkpoint()
            else:
                trainer.durable_journal.publish(
                    ledger.state_dict(), trainer.checkpoint_path, safe=True
                )
        finally:
            import asyncio

            asyncio.run(trainer.durable_journal.aclose())
        initial_checkpoint = destination / "initial_checkpoint.pt"
        if not initial_checkpoint.exists():
            copy_exact(trainer.checkpoint_path, initial_checkpoint)
        elif _sha(initial_checkpoint) != _sha(trainer.checkpoint_path):
            raise ValueError("initial checkpoint cannot be replaced")
        _write(destination / "experiment_manifest.json", prepared.manifest())
        _write(
            destination / "pilot_forecast.json",
            {
                "projection": forecast,
                "reviewed": False,
                "pilot_budget_usd": 5.0,
                "historical_expenditure_included": False,
                "uncertainty_policy_live_validated": False,
            },
        )
        _write(
            destination / "pilot_authorization_pending.json",
            stage_authorization_template(**hooks._authorization_bindings("pilot")),
        )
        receipt = {
            "protocol": CAMPAIGN_PROTOCOL,
            **entry,
            "lifecycle": "registered",
            "checkpoint_path": str(trainer.checkpoint_path),
            "checkpoint_sha256": _sha(trainer.checkpoint_path),
            "parameter_receipts": parameter_fingerprints(trainer),
            "phase": trainer.state.phase,
            "operational_balances": {
                stage: ledger.stage_summary(stage)
                for stage in ("pilot", "development", "test", "compatibility")
            },
            "policy_activated": False,
            "generation_authorized": False,
            "imported_execution_state": False,
        }
        _write(destination / "initialization.json", receipt)
        entry["lifecycle"] = "registered"
        registry["campaigns"][campaign_id] = entry
        _write(registry_path, registry)
    build_independent_policy_plan(manifest, destination)
    return receipt
