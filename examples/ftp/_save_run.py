import base64
import json
from datetime import datetime
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results"


def _save_run(name: str, workflow_output) -> Path:
    """
    Persist workflow output to results/<name>_<timestamp>/.

    Saves:
      result.json  — all non-binary fields (ok, error, page, results, etc.)
      screenshot.png — decoded from base64 data URL when present

    Returns the run directory path.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"{name}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = workflow_output.result
    # End without inputs_schema wraps output as {"output": <data>}
    inner = payload.get("output") if isinstance(payload, dict) else None
    if inner is None and isinstance(payload, dict):
        inner = payload

    summary = {
        "state": str(workflow_output.state),
        "result": serializable,
    }
    (run_dir / "result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    return run_dir
