"""Offline smoke, local-data preparation, and explicitly requested experiment runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .schemas import Scheme, Trajectory


def configure_logging() -> None:
    """Keep machine-readable results on stdout and avoid implicit SDK log files."""
    from openjiuwen.core.common.logging import LogManager
    from openjiuwen.core.common.logging.log_config import configure_log_config

    configure_log_config(
        {
            "backend": "default",
            "level": "WARNING",
            "propagate": False,
            "output": [],
            "interface_output": [],
            "performance_output": [],
        }
    )
    for logger in LogManager.get_all_loggers().values():
        logger.add_handler(logging.StreamHandler(sys.stderr))


def smoke_summary(trajectory: Trajectory) -> dict[str, object]:
    sessions = trajectory.sessions
    traces = [trace for session in sessions for trace in session.traces]
    judge_outputs = {id(output) for trace in traces if trace.tag.scheme is Scheme.JUDGE for output in trace.outputs}
    return {
        "completed": trajectory.completed,
        "example_id": trajectory.example.id,
        "final_answer": trajectory.final_answer,
        "realized_sessions": trajectory.realized_sessions,
        "termination_reasons": [session.termination.reason for session in sessions],
        "conversation_count": len(traces),
        "judge_edges": sum(trace.tag.scheme is Scheme.JUDGE for trace in traces),
        "judge_generations": len(judge_outputs),
        "nonjudge_edges": sum(trace.tag.scheme is not Scheme.JUDGE for trace in traces),
        "zero_labels": sum(scheme is Scheme.ZERO for session in sessions for scheme in session.routing.matrix.values()),
        "fallback_answers": sum(
            record.fallback
            for session in sessions
            for records in session.effective_terminal_answers.values()
            for record in records
        ),
        "weighted_answers": [session.weighted_answer for session in sessions],
        "model_calls": trajectory.usage.model_calls,
    }


async def _smoke() -> int:
    from .workflows.runtime import run_fake_smoke

    trajectory = await run_fake_smoke()
    print(json.dumps(smoke_summary(trajectory), indent=2, sort_keys=True))
    return 0 if trajectory.completed and trajectory.realized_sessions >= 2 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive multi-agent collaboration")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("smoke", help="run the deterministic offline two-session fixture")
    assets = subcommands.add_parser("assets", help="plan, inspect, or explicitly approve pinned local assets")
    assets.add_argument("--directory", help="asset directory inside this example")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    asset_commands.add_parser("plan")
    asset_commands.add_parser("status")
    fetch = asset_commands.add_parser("fetch")
    fetch.add_argument("--authorization", required=True, help="approval of the exact download plan")
    materialize = asset_commands.add_parser("materialize")
    materialize.add_argument("--manifest", help="freeze the local experiment and its physical/active/pilot manifests")
    preflight = subcommands.add_parser(
        "preflight", help="report missing prerequisites without downloads or model construction"
    )
    preflight.add_argument("specification", help="local experiment JSON path")
    preflight.add_argument("--operation", choices=("compatibility", "pilot", "train", "test"), default="pilot")
    preflight.add_argument("--authorization")
    preflight.add_argument("--checkpoint")
    preflight.add_argument("--bootstrap-only", action="store_true")
    preflight.add_argument("--bootstrap-forecast")
    preflight.add_argument("--supplement-plan")
    preflight.add_argument("--supplement-forecast")
    preflight.add_argument("--calibration-only", action="store_true")
    preflight.add_argument("--calibration-plan")
    preflight.add_argument("--calibration-forecast")
    preflight.add_argument("--regular-plan")
    preflight.add_argument("--regular-forecast")
    preflight.add_argument("--extension-scope")
    preflight.add_argument("--extension-scope-authorization")
    finalize = subcommands.add_parser(
        "finalize-evidence", help="record the canonical compatibility findings without changing execution settings"
    )
    finalize.add_argument("specification")
    finalize.add_argument("--report", required=True)
    project = subcommands.add_parser("project", help="offline spending scenarios, without API calls")
    project.add_argument("specification")
    project.add_argument("--pilot-report")
    policy_activate = subcommands.add_parser(
        "policy-activate", help="establish a separately approved bounded-failure condition; no generation"
    )
    policy_activate.add_argument("--plan", required=True)
    policy_activate.add_argument("--authorization", required=True)
    amend = subcommands.add_parser("supplement-transition", help="apply an approved prospective W continuation offline")
    amend.add_argument("specification")
    amend.add_argument("--plan", required=True)
    amend.add_argument("--forecast", required=True)
    amend.add_argument("--authorization", required=True)
    calibration = subcommands.add_parser("calibration-transition", help="activate an approved calibration limit amendment offline")
    calibration.add_argument("specification")
    calibration.add_argument("--plan", required=True)
    calibration.add_argument("--forecast", required=True)
    calibration.add_argument("--authorization", required=True)
    regular = subcommands.add_parser("regular-transition", help="publish an approved TextGrad recovery amendment offline")
    regular.add_argument("specification")
    regular.add_argument("--plan", required=True)
    regular.add_argument("--forecast", required=True)
    regular.add_argument("--authorization", required=True)
    recovery = subcommands.add_parser(
        "publication-recover", help="recover an exactly authorized interrupted offline policy publication")
    recovery.add_argument("specification")
    recovery.add_argument("--plan", required=True)
    recovery.add_argument("--forecast", required=True)
    recovery.add_argument("--authorization", required=True)
    recovery.add_argument("--recovery-authority", required=True)
    archive = subcommands.add_parser("archive-campaign", help="verify and retire an explicitly approved local campaign")
    archive.add_argument("--plan", required=True)
    initialize = subcommands.add_parser("initialize-pilot", help="register fresh unactivated pilot state offline")
    initialize.add_argument("specification")
    initialize.add_argument("--output", required=True)
    initialize.add_argument("--shared-evidence", required=True)
    initialize.add_argument("--source-sha256", required=True)
    initialize.add_argument("--source-attachment")
    initialize.add_argument("--authorization", required=True)
    for command in ("extension-validate", "extension-prepare", "extension-adapt", "extension-test"):
        command_parser = subcommands.add_parser(command, help="explicitly registered incremental extension")
        command_parser.add_argument("registration")
        if command != "extension-validate":
            command_parser.add_argument("--output", required=True)
        if command == "extension-adapt":
            command_parser.add_argument("--authorization", required=True)
            command_parser.add_argument("--development-authorization", required=True)
            command_parser.add_argument("--resume", required=True)
            command_parser.add_argument("--escalation-scope")
            command_parser.add_argument("--escalation-authorization")
        if command == "extension-test":
            command_parser.add_argument("--checkpoint", required=True)
            command_parser.add_argument("--authorization", required=True)
    retire = subcommands.add_parser("retire-directory", help="remove a verified closed campaign's former directory")
    retire.add_argument("--campaign", required=True)
    retire.add_argument("--authorization", required=True)
    accounting = subcommands.add_parser(
        "reconcile-accounting", help="perform only the approved bounded historical generation-metadata lookups"
    )
    accounting.add_argument("--campaign", required=True)
    accounting.add_argument("--audit", required=True)
    accounting.add_argument("--authorization", required=True)
    accounting.add_argument("--disposition", required=True)
    accounting.add_argument("--dispatch-id", type=int, action="append", required=True,
                            help="exact authorized physical request ID (repeat for up to three)")
    settlement = subcommands.add_parser(
        "settle-accounting", help="adjudicate saved metadata receipts using frozen public identity evidence; no network"
    )
    settlement.add_argument("--campaign", required=True)
    settlement.add_argument("--audit", required=True)
    settlement.add_argument("--model-catalog", required=True)
    settlement.add_argument(
        "--endpoint-catalog",
        action="append",
        required=True,
        metavar="MODEL=PATH",
        help="exact requested model identifier and its saved endpoint catalog (repeat per model)",
    )
    prepare = subcommands.add_parser("prepare", help="inspect a local experiment and its isolated query partitions")
    prepare.add_argument("specification", help="local experiment JSON path")
    for command, help_text in (
        ("compatibility", "run the separately approved bounded synthetic checks"),
        ("pilot", "run only the eight-query training pilot and stop"),
        ("train", "run separately approved development from a completed pilot checkpoint"),
    ):
        stage = subcommands.add_parser(command, help=help_text)
        stage.add_argument("specification")
        stage.add_argument("--output", required=True, help="registered campaign directory")
        stage.add_argument("--resume", required=command == "train", help="compatible safe-boundary checkpoint")
        stage.add_argument("--authorization", required=True, help="exact separate stage approval artifact")
        if command == "train":
            stage.add_argument("--bootstrap-only", action="store_true",
                               help="stop after W then routing bootstrap validation, before calibration")
            stage.add_argument("--bootstrap-forecast", help="separately reviewed bootstrap spending forecast")
            stage.add_argument("--supplement-plan", help="audited supplementary W continuation")
            stage.add_argument("--supplement-forecast", help="approved supplementary workload and headroom")
            stage.add_argument("--calibration-only", action="store_true", help="calibrate, publish checkpoint0 and stop before cycle1")
            stage.add_argument("--calibration-plan", help="audited uncertainty-limit amendment")
            stage.add_argument("--calibration-forecast", help="approved shared-prefix calibration workload")
            stage.add_argument("--regular-plan", help="audited regular-development TextGrad amendment")
            stage.add_argument("--regular-forecast", help="approved finite regular-development workload")
    test = subcommands.add_parser(
        "test", help="evaluate a completed selected checkpoint under its frozen test protocol"
    )
    test.add_argument("specification", help="the same local experiment JSON used for training")
    test.add_argument("--checkpoint", required=True)
    test.add_argument("--output", required=True)
    test.add_argument("--authorization", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    result: Any
    if args.command.startswith("extension-"):
        from .operations.extension_experiment import (
            validate_extension_registration, prepare_extension, adapt_extension, evaluate_extension,
        )
        if args.command == "extension-validate":
            result = validate_extension_registration(args.registration)
        elif args.command == "extension-prepare":
            result = prepare_extension(args.registration, args.output)
        elif args.command == "extension-adapt":
            from .infrastructure.shutdown import run_stage
            result = run_stage(adapt_extension(args.registration, args.output,
                authorization=args.authorization, development_authorization=args.development_authorization,
                resume=args.resume, escalation_scope=args.escalation_scope,
                escalation_authorization=args.escalation_authorization))
        else:
            from .infrastructure.shutdown import run_stage
            result = run_stage(evaluate_extension(args.registration, args.checkpoint, args.output,
                authorization=args.authorization))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("stage_complete", result.get("accepted", True)) else 1
    if args.command == "retire-directory":
        from .operations.campaigns import remove_closed_campaign_directory
        result = remove_closed_campaign_directory(args.campaign,
            authorization=json.loads(Path(args.authorization).read_text()))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "archive-campaign":
        from .operations.campaigns import archive_campaign

        plan = json.loads(Path(args.plan).read_text())
        result = archive_campaign(**plan)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "initialize-pilot":
        from .operations.campaigns import initialize_pilot

        result = initialize_pilot(
            args.specification,
            args.output,
            shared_evidence_path=args.shared_evidence,
            specification_sha256=args.source_sha256,
            specification_attachment=args.source_attachment,
            authorization=json.loads(Path(args.authorization).read_text()),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "policy-activate":
        from .operations.policy_activation import activate_policy_plan

        result = activate_policy_plan(args.plan, args.authorization)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "settle-accounting":
        from .operations.reconciliation import settle_cached_receipts

        endpoints: dict[str, str] = {}
        for item in args.endpoint_catalog:
            model, separator, path = item.partition("=")
            if not separator or not model or not path or model in endpoints:
                raise ValueError("endpoint catalogs require unique MODEL=PATH pairs")
            endpoints[model] = path
        result = asyncio.run(
            settle_cached_receipts(
                args.campaign,
                args.audit,
                model_catalog_path=args.model_catalog,
                endpoint_catalog_paths=endpoints,
            )
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("fully_resolved") is True else 1
    if args.command == "reconcile-accounting":
        import os
        from functools import partial
        from .providers.gateway import lookup_generation_metadata
        from .operations.reconciliation import reconcile_campaign

        result = asyncio.run(
            reconcile_campaign(
                args.campaign,
                args.audit,
                lookup=partial(lookup_generation_metadata, api_key=os.environ["OPENROUTER_API_KEY"]),
                authorization=json.loads(Path(args.authorization).read_text(encoding="utf-8")),
                dispatch_ids=tuple(args.dispatch_id),
                disposition=args.disposition,
            )
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("fully_resolved") is True else 1
    if args.command == "smoke":
        return asyncio.run(_smoke())
    if args.command == "assets":
        from .datasets.assets import (
            TARGET_DIRECTORY,
            asset_plan,
            download_approval_template,
            download_assets,
            freeze_strategyqa_manifest,
            inspect_assets,
            materialize_strategyqa,
        )

        directory = args.directory or TARGET_DIRECTORY / "assets"
        if args.asset_command == "plan":
            plan = asset_plan(directory)
            result = {"plan": plan, "authorization_template": download_approval_template(plan)}
        elif args.asset_command == "status":
            result = inspect_assets(directory)
        elif args.asset_command == "fetch":
            result = download_assets(directory, args.authorization)
        else:
            if args.manifest:
                result = freeze_strategyqa_manifest(args.manifest, directory)
            else:
                result = materialize_strategyqa(directory)
                result = {key: value for key, value in result.items() if key != "ordered_ids"}
                result["protected_id_count"] = 100
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "supplement-transition":
        from .operations.supplement import apply_transition
        result = apply_transition(args.specification, args.plan, args.authorization, args.forecast)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "publication-recover":
        from .operations.publication_recovery import recover_interrupted
        result = recover_interrupted(args.specification, args.plan, args.forecast,
                                     args.authorization, args.recovery_authority)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "regular-transition":
        from .operations.regular_development import apply_transition
        result = apply_transition(args.specification, args.plan, args.authorization, args.forecast)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "calibration-transition":
        from .operations.calibration_launch import apply_transition
        result = apply_transition(args.specification, args.plan, args.authorization, args.forecast)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "preflight":
        from .operations.preflight import inspect_preflight

        scope = {}
        if args.extension_scope is not None or args.extension_scope_authorization is not None:
            if args.extension_scope is None or args.extension_scope_authorization is None:
                raise ValueError("prospective extension preflight requires both scope and its approval")
            scope = {"extension_scope": args.extension_scope, "extension_scope_authorization":
                     json.loads(Path(args.extension_scope_authorization).read_text())}
        if args.supplement_plan is not None or args.supplement_forecast is not None:
            scope.update(supplement_plan_path=args.supplement_plan, supplement_forecast_path=args.supplement_forecast)
        if args.calibration_only or args.calibration_plan is not None or args.calibration_forecast is not None:
            scope.update(calibration_only=args.calibration_only, calibration_plan_path=args.calibration_plan,
                         calibration_forecast_path=args.calibration_forecast)
        if args.regular_plan is not None or args.regular_forecast is not None:
            scope.update(regular_plan_path=args.regular_plan, regular_forecast_path=args.regular_forecast)
        report = inspect_preflight(
            args.specification,
            operation=args.operation,
            authorization_path=args.authorization,
            checkpoint_path=args.checkpoint,
            bootstrap_only=args.bootstrap_only,
            bootstrap_forecast_path=args.bootstrap_forecast,
            **scope,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.ready else 2
    if args.command == "prepare":
        from .operations.experiment import prepare_experiment

        prepared = prepare_experiment(args.specification)
        print(
            json.dumps(
                prepared.manifest(),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "finalize-evidence":
        from .operations.preflight import finalize_compatibility_evidence

        print(json.dumps(finalize_compatibility_evidence(args.specification, args.report), indent=2, sort_keys=True))
        return 0
    if args.command == "project":
        from .operations.projection import spending_projection

        specification = json.loads(Path(args.specification).read_text(encoding="utf-8"))
        pilot_report = json.loads(Path(args.pilot_report).read_text(encoding="utf-8")) if args.pilot_report else None
        compatibility_report = None
        if specification.get("attestations", {}).get("compatibility", {}).get("state") == "validated":
            from .operations.preflight import validate_finalized_compatibility

            compatibility_report = validate_finalized_compatibility(specification, Path(args.specification).resolve())
        print(
            json.dumps(
                spending_projection(specification, pilot_report, compatibility_report=compatibility_report),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command in {"compatibility", "pilot", "train"}:
        from .operations.experiment import compatibility_experiment, pilot_experiment, run_experiment
        from .infrastructure.shutdown import run_stage, StageInterrupted

        runners: dict[str, Callable[..., Any]] = {
            "compatibility": compatibility_experiment,
            "pilot": pilot_experiment,
            "train": run_experiment,
        }
        runner = runners[args.command]
        scope = ({"bootstrap_only": args.bootstrap_only, "bootstrap_forecast_path": args.bootstrap_forecast}
                 if args.command == "train" else {})
        if args.command == "train":
            if args.supplement_plan is not None or args.supplement_forecast is not None:
                scope.update(supplement_plan_path=args.supplement_plan, supplement_forecast_path=args.supplement_forecast)
            if args.calibration_only or args.calibration_plan is not None or args.calibration_forecast is not None:
                scope.update(calibration_only=args.calibration_only, calibration_plan_path=args.calibration_plan,
                             calibration_forecast_path=args.calibration_forecast)
            if args.regular_plan is not None or args.regular_forecast is not None:
                scope.update(regular_plan_path=args.regular_plan, regular_forecast_path=args.regular_forecast)
        try:
            result = run_stage(
                runner(args.specification, args.output, resume=args.resume, authorization_path=args.authorization, **scope)
            )
        except StageInterrupted as exc:
            print(json.dumps({
                "phase": "interrupted", "stage_complete": False, "reason": str(exc),
                "result_path": str((Path(args.output) / "result.json").resolve()),
                "resume_safe": False,
                "persistence_errors": exc.cleanup_errors,
                "stop_cause": exc.state.cause,
                "operator_signals": list(exc.state.signals),
            }, indent=2, sort_keys=True))
            return 128 + (exc.state.signum or 2)
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.command == "compatibility":
            return 0 if result.get("status") == "complete" and result.get("assessment", {}).get("passed") else 1
        return 0 if result.get("stage_complete") is True else 1
    if args.command == "test":
        from .operations.experiment import evaluate_experiment
        from .infrastructure.shutdown import run_stage, StageInterrupted

        try:
            result = run_stage(
                evaluate_experiment(args.specification, args.checkpoint, args.output, authorization_path=args.authorization)
            )
        except StageInterrupted as exc:
            print(json.dumps({
                "phase": "interrupted", "stage_complete": False, "reason": str(exc),
                "stop_cause": exc.state.cause, "operator_signals": list(exc.state.signals),
            }))
            return 128 + (exc.state.signum or 2)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("stage_complete") is True else 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
