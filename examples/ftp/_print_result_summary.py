from pathlib import Path


def _print_result_summary(name: str, result, run_dir: Path):
    """Print a concise summary of workflow execution results."""
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    print(f"  state      : {result.state}")
    print(f"  saved to   : {run_dir}")

    inner = (result.result or {}).get("output", result.result) if isinstance(result.result, dict) else {}
    if isinstance(inner, dict):
        print(f"  ok         : {inner.get('ok')}")
        if inner.get("error"):
            print(f"  error      : {inner.get('error')}")
        if inner.get("final"):
            final_text = str(inner.get("final", ""))[:150]
            print(f"  final      : {final_text}{'...' if len(str(inner.get('final', ''))) > 150 else ''}")
        if inner.get("results"):
            print(f"  action results:")
            for ar in inner.get("results", []):
                action = ar.get("action", "")
                ok = ar.get("ok", False)
                data_preview = str(ar.get("data", ""))[:80]
                print(f"    - {action:12s}  ok={ok}  data={data_preview}")
        if inner.get("page"):
            page = inner["page"]
            if isinstance(page, dict):
                print(f"  page       : {page.get('url', '')} | {page.get('title', '')}")
