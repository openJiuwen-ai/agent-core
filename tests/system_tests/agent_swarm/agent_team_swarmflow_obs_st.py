# coding: utf-8
"""Swarmflow + observability system test (otlp_grpc direct export).

A minimal, self-verifying ST that:
  1. wires the model endpoint from ``.env`` (no config_llm_local.yaml needed —
     that file is gitignored and absent, which is what breaks
     ``agent_team_swarmflow_e2e.py``);
  2. enables observability with ``exporter="otlp_grpc"`` pointing at the local
     OTel collector (``http://localhost:4317``) — spans flow straight to
     Langfuse via the collector, **no file-then-upload two-step**;
  3. asks the leader to run a tiny inline swarmflow (``sum-verify``: two
     parallel ``agent()`` calls + one summary call) via the swarmflow tool's
     inline ``script`` parameter — no swarmskill-creator skill, no disk
     script_path;
  4. drains the leader stream with a hard 3-minute ceiling, then verifies the
     trace tree through the Langfuse REST API: team span is ROOT, worker
     ``agent.wf-worker*`` spans exist under it, ``llm.call`` spans sit under
     the workers, no orphans.

Run (with output captured to a file + grep for errors, to avoid re-running
on lost output):

    cd D:/workdir/agent-core
    timeout 200 python tests/system_tests/agent_swarm/agent_team_swarmflow_obs_st.py \\
        > tests/system_tests/agent_swarm/.obs_st_output.log 2>&1
    echo "exit=$?"
    grep -iE "error|traceback|exception|fail" tests/system_tests/agent_swarm/.obs_st_output.log | head -30

Prerequisites:
  1. ``.env`` with API_BASE / LEADER_API_KEY / MODEL_PROVIDER / MODEL_NAME.
  2. ``deploy/observability`` docker-compose up (otel-collector:4317 +
     langfuse-web:3000, project pk-lf-jiuwen / sk-lf-jiuwen).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path + env bootstrap (mirror agent_team_observability_verify_interact.py)
# ---------------------------------------------------------------------------
_AGENT_CORE = Path("D:/workdir/agent-core")
sys.path.insert(0, str(_AGENT_CORE))
sys.path.insert(0, str(_AGENT_CORE / "tests/system_tests/agent_swarm"))

# Source the model endpoint from .env (gitignored, holds the real keys).
_ENV_PATH = _AGENT_CORE / ".env"
if _ENV_PATH.is_file():
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key, val)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# ---------------------------------------------------------------------------
# Isolate the DB: point openjiuwen home at a fresh temp directory so the ST
# never hits the real ~/.openjiuwen with its 487 historical team_message_*
# tables (migration took 76s last run). The temp dir is cleaned up on exit.
# Must happen before any openjiuwen import that reads paths, because
# ``get_agent_teams_home()`` is resolved lazily from this global.
# ---------------------------------------------------------------------------
_TEMP_HOME = tempfile.mkdtemp(prefix="obs_st_home_")
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home

configure_openjiuwen_home(_TEMP_HOME)
print(f"[DB Isolation] openjiuwen home → {_TEMP_HOME} (temp, empty)", flush=True)

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.common.logging.log_config import configure_log_config
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

# ---------------------------------------------------------------------------
# Per-run identity (unique so Langfuse never confuses this run with a prior one)
# ---------------------------------------------------------------------------
_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
_RAND = uuid.uuid4().hex[:6]
TEAM_NAME = f"swarmflow_obs_st_{_TS}_{_RAND}"
SESSION_ID = f"swarmflow_obs_st_sess_{_TS}_{_RAND}"

# Langfuse REST API (Basic auth with the project keys baked into docker-compose).
LF_BASE = "http://localhost:3000"
LF_PK = "pk-lf-jiuwen"
LF_SK = "sk-lf-jiuwen"

# ---------------------------------------------------------------------------
# Team config — minimal: leader + teammate, swarmflow enabled.
# teammate carries no tools: the sum-verify workflow only does schema-free
# LLM calls, so workers need nothing beyond the model. Mirrors config_swarmflow.yaml.
# ---------------------------------------------------------------------------
TEAM_YAML_STR = """
team_name: TEAM_NAME_PLACEHOLDER
lifecycle: persistent
spawn_mode: inprocess
enable_swarmflow: true

leader:
  member_name: leader
  display_name: TeamLeader
  persona: "团队领导，负责使用 swarmflow 工具执行简单工作流并汇报结果"

agents:
  leader:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_base: "${API_BASE}"
        api_key: "${LEADER_API_KEY}"
        timeout: 120
        rate_limit: 5.0
        verify_ssl: false
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.3
    max_iterations: 40
    completion_timeout: 300.0
    language: cn

  teammate:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_base: "${API_BASE}"
        api_key: "${LEADER_API_KEY}"
        timeout: 120
        rate_limit: 5.0
        verify_ssl: false
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.3
    max_iterations: 40
    completion_timeout: 300.0
    language: cn

transport:
  type: inprocess

storage:
  type: sqlite
"""
TEAM_YAML_STR = TEAM_YAML_STR.replace("TEAM_NAME_PLACEHOLDER", TEAM_NAME)

# ---------------------------------------------------------------------------
# The inline swarmflow script the leader will run via the swarmflow tool's
# `script` parameter. sum-verify: 2 parallel agent() (1+1, 2+2) + 1 summary.
# Three LLM calls → three agent.wf-worker* spans + three llm.call spans.
# (Same shape as the previously-generated
#  .../workflows/sum-verify/script.py, kept inline so no disk path is needed.)
# ---------------------------------------------------------------------------
SWARMFLOW_SCRIPT = """from swarmflow import agent, parallel, phase, compact

META = {
    "name": "sum-verify",
    "description": "并行计算 1+1 和 2+2，再汇总相加",
    "phases": [{"title": "并行计算"}, {"title": "汇总相加"}]
}

async def run(args):
    phase("并行计算")
    results = await parallel([
        lambda: agent("请计算 1+1 等于多少？只返回数字。", label="1+1", phase="并行计算"),
        lambda: agent("请计算 2+2 等于多少？只返回数字。", label="2+2", phase="并行计算"),
    ])
    nums = []
    for r in results:
        if r:
            for word in r.strip().split():
                try:
                    nums.append(int(word))
                    break
                except ValueError:
                    continue
    if len(nums) < 2:
        return {"错误": f"无法从结果中解析数字: {results}"}
    a, b = nums[0], nums[1]
    phase("汇总相加")
    final = await agent(f"请计算 {a}+{b} 等于多少？两个数分别是 {a} 和 {b}，算出它们的和，只返回数字。", label="汇总", phase="汇总相加")
    return {
        "第一步_1+1": a,
        "第一步_2+2": b,
        "第二步_汇总": final.strip() if final else "无结果",
        "验证": f"{a}+{b}={final.strip() if final else '?'}"
    }
"""

# ---------------------------------------------------------------------------
# Timing knobs
# ---------------------------------------------------------------------------
# Hard ceiling on the whole leader stream. The sum-verify flow is a handful of
# fast LLM calls (~30-60s on a flash model); 180s leaves ample margin while
# guaranteeing the ST never hangs the runner.
_RUN_TIMEOUT_S = 180.0
# How long to wait after the run for spans to clear the OTel batch processor
# (5s default) + collector groupbytrace (2s) + Langfuse ingest before querying.
_EXPORT_SETTLE_S = 25.0
# After the leader narrates the swarmflow result, wait for the stream to go
# quiet this long (persistent lifecycle never self-completes), then tear down.
# Floor that also lets the async-tool result injection land when the leader
# narrates nothing extra; capped by _NARRATION_MAX_S.
_NARRATION_QUIESCE_S = 6.0
_NARRATION_MAX_S = 30.0


def lf_get(path: str) -> dict | None:
    """GET a Langfuse public API path with Basic auth; None on failure."""
    url = f"{LF_BASE}/api/public/{path}"
    req = urllib.request.Request(url)
    req.add_header(
        "Authorization",
        f"Basic {base64.b64encode(f'{LF_PK}:{LF_SK}'.encode()).decode()}",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=6).read())
    except Exception as e:  # noqa: BLE001 - surface, don't crash the ST
        print(f"  [Langfuse API] {type(e).__name__}: {e}")
        return None


def _obs_attrs(obs: dict) -> dict:
    """Extract the OTel attributes dict from a Langfuse observation.

    Langfuse v3 serialises ``metadata`` as a JSON **string** (e.g. ``"{}"``),
    not a dict, so a plain ``.get("metadata", {}).get("attributes")`` crashes.
    """
    meta = obs.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            return {}
    if not isinstance(meta, dict):
        return {}
    attrs = meta.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


async def run_and_verify() -> bool:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

    # --- Observability: otlp_grpc straight to the local collector. ---
    obs_config = ObservabilityConfig(
        enabled=True,
        service_name="openjiuwen-swarmflow-obs-st",
        exporter="otlp_grpc",
        endpoint="http://localhost:4317",
        sample_rate=1.0,
    )
    init_observability(obs_config)
    team_logger.info(
        "observability: enabled={} exporter={} endpoint={}",
        obs_config.enabled, obs_config.exporter, obs_config.endpoint,
    )
    print(f"[Obs] exporter=otlp_grpc endpoint={obs_config.endpoint}", flush=True)

    # --- Build the team spec (env-expand the ${VAR} placeholders). ---
    import yaml
    from _e2e_utils import expand_env_vars

    cfg = expand_env_vars(yaml.safe_load(TEAM_YAML_STR))
    cfg.pop("runtime", None)
    spec = TeamAgentSpec.model_validate(cfg)

    # The leader runs the inline swarmflow via the swarmflow tool's `script`
    # parameter. We hand it the full source and insist it only call this one
    # tool, then wait — the workflow result is injected back as a follow-up
    # message, not a suspended tool_result.
    query = (
        "请立即调用 swarmflow 工具来运行一个工作流。"
        "使用 script 参数（内联脚本源码，不要用 script_path），"
        "script 内容为下方 ```python 代码块里的完整脚本，args 留空。"
        "只需调用这一个工具，然后等待工作流结果并简要汇报，不要自己拆解或执行其它步骤。\n"
        "```python\n" + SWARMFLOW_SCRIPT + "```"
    )

    await Runner.start()

    print(f"\n{'='*60}")
    print(f"Swarmflow+Obs ST — Team: {TEAM_NAME}  Session: {SESSION_ID}")
    print(f"{'='*60}\n", flush=True)

    # --- Stream consumption ---
    # Notes from the first run (output.log):
    #   * ``run_agent_team_streaming`` does NOT yield a ``tool_call`` chunk for the
    #     leader's swarmflow invocation — so we cannot set a "launched" flag from
    #     chunk types. The authoritative signal that the workflow ran is the
    #     leader's narrated result answer (it echoes run_id / sum-verify results).
    #   * With ``lifecycle: persistent`` the team never emits ``team_completed``
    #     on its own — it parks in idle waiting for an external stop. The async
    #     ``for`` over the stream blocks on the next chunk, so a wall-clock
    #     quiescence check inside the loop can't fire while blocked. Instead a
    #     separate watcher task cancels the stream once the leader has narrated
    #     the result AND gone quiet for _NARRATION_QUIESCE_S — turning a 3-min
    #     hang into a ~30s run while still letting the async-tool result
    #     injection land before teardown.
    answer_count = 0
    swarmflow_result_seen = False   # leader narrated the workflow's result
    team_completed = False
    stream_error: BaseException | None = None
    last_answer_time: list[float] = [0.0]   # one-elem cell for the watcher

    async def consume_stream() -> None:
        nonlocal answer_count, swarmflow_result_seen, team_completed, stream_error
        try:
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": query},
                session=SESSION_ID,
            ):
                chunk_type = getattr(chunk, "type", "")
                payload = getattr(chunk, "payload", None)

                if chunk_type == "answer":
                    text = ""
                    if isinstance(payload, dict):
                        text = payload.get("output", "") or payload.get("content", "")
                    elif isinstance(payload, str):
                        text = payload
                    if text:
                        answer_count += 1
                        last_answer_time[0] = time.monotonic()
                        print(f"  [Answer #{answer_count}] {text[:200]}", flush=True)
                        # The leader narrates the swarmflow result once it's
                        # injected back ("sum-verify 已完成" / "2+4=6" / run_id).
                        if any(
                            marker in text
                            for marker in ("sum-verify", "2 + 4", "2+4", "汇总", "工作流")
                        ):
                            swarmflow_result_seen = True

                if chunk_type == "team_completed":
                    team_completed = True
                    print("  [Team] Completed!", flush=True)
                    return
        except asyncio.CancelledError:
            # Watcher-initiated teardown — expected, not an error.
            return
        except Exception as e:  # noqa: BLE001 - record, don't lose the trace
            stream_error = e
            print(f"  [Stream ERROR] {type(e).__name__}: {e}", flush=True)

    stream_task = asyncio.create_task(consume_stream())

    async def quiesce_watcher() -> None:
        """Cancel the stream once the result is narrated and the leader goes quiet.

        Persistent teams never self-complete, so without this the stream parks
        in idle until the hard timeout. We wait for the result, then for a quiet
        window (no new answer) — the injection that produced the answer has
        already landed, so cancelling never races a pending send.
        """
        start = time.monotonic()
        while not stream_task.done():
            if time.monotonic() - start > _RUN_TIMEOUT_S:
                print(f"  [Stop] hard timeout ({_RUN_TIMEOUT_S}s) — no result narrated",
                      flush=True)
                stream_task.cancel()
                return
            if swarmflow_result_seen:
                quiet_for = time.monotonic() - last_answer_time[0]
                if quiet_for >= _NARRATION_QUIESCE_S:
                    print(f"  [Stop] result narrated, quiet {quiet_for:.1f}s — "
                          "tearing down", flush=True)
                    stream_task.cancel()
                    return
            await asyncio.sleep(0.5)

    watcher_task = asyncio.create_task(quiesce_watcher())
    try:
        await asyncio.wait({stream_task, watcher_task},
                           timeout=_RUN_TIMEOUT_S + 10,
                           return_when=asyncio.FIRST_COMPLETED)
    except asyncio.TimeoutError:
        pass
    for t in (watcher_task, stream_task):
        if not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    try:
        await Runner.stop()
    except Exception as e:  # noqa: BLE001
        print(f"  [Runner.stop ERROR] {type(e).__name__}: {e}", flush=True)
    shutdown_observability()
    print("\n  Team session ended.", flush=True)

    # --- Run-phase verdict (separate from observability verdict below). ---
    print(f"\n  [Run phase] answers={answer_count} "
          f"swarmflow_result_seen={swarmflow_result_seen} "
          f"team_completed={team_completed}", flush=True)
    if stream_error is not None:
        print(f"  [Run phase] stream crashed: {stream_error!r}", flush=True)
    if not swarmflow_result_seen:
        print("  FAIL: leader never narrated a swarmflow result", flush=True)
        return False

    # --- Observability verification via Langfuse REST API. ---
    print(f"\n  Waiting {_EXPORT_SETTLE_S}s for spans to export to Langfuse...", flush=True)
    time.sleep(_EXPORT_SETTLE_S)

    print(f"\n{'='*60}")
    print("Langfuse Trace Verification (swarmflow)")
    print(f"{'='*60}\n", flush=True)

    traces_data = lf_get("traces?limit=30")
    if traces_data is None:
        print("FAIL: cannot reach Langfuse API at", LF_BASE, flush=True)
        return False

    traces = traces_data.get("data", [])
    print(f"  Recent traces: {len(traces)}", flush=True)

    # Find our trace. The OTel collector names the trace after the root team
    # span (``team.<TEAM_NAME>``), so ``TEAM_NAME in trace.name`` matches
    # directly — fetch ONLY those by detail. Fall back to scanning the
    # observation ``agentteam.team.name`` attribute on the first few traces
    # only (bounded, so a slow Langfuse never hangs the ST).
    our_traces: list[dict] = []
    name_hits = [t for t in traces if TEAM_NAME in (t.get("name") or "")]
    print(f"  name matches: {len(name_hits)}", flush=True)
    for t in name_hits:
        td = lf_get(f"traces/{t.get('id')}")
        if td is not None:
            our_traces.append(td)

    if not our_traces:
        # Bounded fallback: only the 8 most recent traces, only until found.
        print("  no name match; bounded attr fallback (up to 8 traces)...", flush=True)
        for t in traces[:8]:
            td = lf_get(f"traces/{t.get('id')}")
            if td is None:
                continue
            for o in td.get("observations", []):
                attrs = _obs_attrs(o)
                if attrs.get("agentteam.team.name") == TEAM_NAME:
                    our_traces.append(td)
                    break
            if our_traces:
                break

    if not our_traces:
        print(f"  FAIL: no traces found for team_name={TEAM_NAME}", flush=True)
        for t in traces[:5]:
            print(f'    name="{t.get("name")}" id={str(t.get("id"))[:16]}', flush=True)
        return False

    print(f"  Found {len(our_traces)} trace(s) for our team\n", flush=True)

    all_pass = True
    for idx, our_trace in enumerate(our_traces):
        trace_name = our_trace.get("name", "")
        trace_id = our_trace.get("id", "")
        observations = our_trace.get("observations", [])
        print(f"  --- Trace #{idx+1}: id={str(trace_id)[:16]} "
              f'name="{trace_name}" obs={len(observations)} ---', flush=True)

        obs_by_id = {o.get("id"): o for o in observations}
        parent_map: dict[str, str] = {}
        roots: list[dict] = []
        for o in observations:
            pid = o.get("parentObservationId", "")
            if not pid:
                roots.append(o)
            else:
                parent_map[o.get("id")] = pid

        def print_tree(obs_id: str, indent: int = 0) -> None:
            o = obs_by_id.get(obs_id)
            if o is None:
                return
            name = o.get("name", "?")
            o_type = o.get("type", "?")
            attrs = _obs_attrs(o)
            member = attrs.get("agentteam.member.name", "")
            # Show whether the observation captured input/output — an llm.call
            # with both null is a leaked span (cancelled, closed by the
            # finalize fallback with no real payload).
            out_raw = o.get("output")
            out_flag = "CANCELLED" if out_raw in ("cancelled", "Cancelled") else (
                "out" if out_raw else "no-out"
            )
            in_flag = "in" if o.get("input") else "no-in"
            prefix = "    " + "  " * indent
            extra = f" member={member}" if member else ""
            data = f" [{in_flag}/{out_flag}]" if o_type == "GENERATION" else ""
            sm = o.get("statusMessage")
            sm_tag = f" ERR400" if sm and "400" in str(sm) else ""
            print(f"{prefix}{name} [{o_type}]{extra}{data}{sm_tag}", flush=True)
            for child_id, child_pid in parent_map.items():
                if child_pid == obs_id:
                    print_tree(child_id, indent + 1)

        for r in roots:
            print_tree(r.get("id"))

        checks: dict[str, bool] = {}

        # 1. team span exists and is a root.
        team_obs = [
            o for o in observations
            if o.get("name", "").startswith("team.") and not o.get("parentObservationId")
        ]
        checks["team_span_is_root"] = len(team_obs) > 0
        if not team_obs:
            print("    !! no root team.* span found", flush=True)

        # 2. worker agent spans exist. Swarmflow worker member ids are
        # ``wf-<runid>-<n>-<n>-<n>`` or ``wf-<runid>-worker-<n>``, so the agent
        # span name is ``agent.wf-...`` — distinct from the leader's
        # ``agent.leader.task_iteration.*``.
        worker_obs = [
            o for o in observations
            if o.get("name", "").startswith("agent.wf-")
        ]
        checks["worker_agent_spans_exist"] = len(worker_obs) > 0
        if not worker_obs:
            print("    !! no agent.wf-* worker spans found", flush=True)
        else:
            print(f"    worker spans: {len(worker_obs)}", flush=True)

        # 3. worker agent spans are children of the team span.
        if team_obs and worker_obs:
            team_id = team_obs[0].get("id")
            under = sum(1 for w in worker_obs if w.get("parentObservationId") == team_id)
            checks["workers_under_team"] = under == len(worker_obs)
            if under != len(worker_obs):
                for w in worker_obs:
                    pid = w.get("parentObservationId", "")
                    pn = obs_by_id.get(pid, {}).get("name", "?") if pid else "ROOT"
                    print(f"    !! worker {w.get('name')} parent={pn}", flush=True)

        # 4. llm.call spans exist, nested under worker agent spans.
        llm_obs = [o for o in observations if o.get("name") == "llm.call"]
        checks["llm_call_spans_exist"] = len(llm_obs) > 0
        if worker_obs and llm_obs:
            worker_ids = {w.get("id") for w in worker_obs}
            llm_under = sum(1 for l in llm_obs if l.get("parentObservationId") in worker_ids)
            checks["llm_under_workers"] = llm_under > 0
            if llm_under == 0:
                print("    !! no llm.call span under any worker agent span", flush=True)

        # 4b. llm.call spans carry a real output (the known swarmflow bug: a
        # worker LLM call cancelled mid-flight never fires the close callback,
        # so finalize stamps its output="cancelled" with no real payload —
        # surfaced here so the ST fails loudly on the regression instead of
        # silently passing on a half-empty trace). Image-probe 400s are
        # excluded: they close via on_llm_call_error (no payload by design).
        _LEAKED_OUT = (None, "", "cancelled", "Cancelled")

        def _is_leaked(o: dict) -> bool:
            return o.get("output") in _LEAKED_OUT

        if llm_obs:
            sm = lambda o: o.get("statusMessage") or ""  # noqa: E731
            real_llms = [l for l in llm_obs if "400" not in sm(l) and "image_url" not in sm(l)]
            leaked = [l for l in real_llms if _is_leaked(l)]
            checks["llm_call_has_payload"] = len(leaked) == 0
            print(f"    llm.call total={len(llm_obs)} real(non-400)={len(real_llms)} "
                  f"with_output={len(real_llms) - len(leaked)} leaked={len(leaked)}",
                  flush=True)
            if leaked:
                print(f"    !! {len(leaked)} llm.call span(s) have no real output "
                      "(cancelled/leaked — see HANDOFF_llm_span_cancel_leak_rootcause.md)",
                      flush=True)

        # 5. no orphan observations.
        obs_ids = {o.get("id") for o in observations}
        orphans = [
            o.get("name", "?") for o in observations
            if o.get("parentObservationId") and o.get("parentObservationId") not in obs_ids
        ]
        checks["no_orphans"] = len(orphans) == 0
        if orphans:
            print(f"    !! orphan observations: {orphans}", flush=True)

        print(f"\n    Checks for trace #{idx+1}:", flush=True)
        for check, result in checks.items():
            status = "PASS" if result else "FAIL"
            if not result:
                all_pass = False
            print(f"      {check}: {status}", flush=True)
        print(flush=True)

    if all_pass:
        print(f"  All checks PASSED! Open Langfuse at {LF_BASE}", flush=True)
    else:
        print(f"  Some checks FAILED. Check Langfuse at {LF_BASE}", flush=True)
    return all_pass


if __name__ == "__main__":
    success = asyncio.run(run_and_verify())
    # Clean up the temp home so stray DB files don't accumulate.
    reset_openjiuwen_home()
    if os.path.isdir(_TEMP_HOME):
        shutil.rmtree(_TEMP_HOME, ignore_errors=True)
        print(f"[DB Isolation] cleaned up temp home {_TEMP_HOME}", flush=True)
    sys.exit(0 if success else 1)
