#!/usr/bin/env python3
"""
Chimera Cortex — Benchmark CLI (HTTP API Wrapper)
==================================================
A thin CLI client that triggers and monitors benchmark runs through
the running web application's HTTP API. All evaluation logic lives in
cortex/benchmark.py and is executed server-side.

Usage:
    python benchmark.py                          # Start a run & poll progress
    python benchmark.py --api-url http://10.0.0.5:8000
    python benchmark.py --judge-model qwen3.5:9b
    python benchmark.py status                   # Show current run status
    python benchmark.py list                     # List all historical runs
    python benchmark.py stop                     # Cancel the active run
"""

import argparse
import sys
import time
import httpx

from cortex.config import DEFAULT_API_URL, DEFAULT_JUDGE_MODEL

# ---------------------------------------------------------------------------
# CLI Helpers
# ---------------------------------------------------------------------------
POLL_INTERVAL = 3  # seconds between status polls

def _api(base: str, method: str, path: str, **kwargs) -> httpx.Response:
    """Perform an HTTP request against the RAG API and exit on hard errors."""
    url = f"{base.rstrip('/')}{path}"
    try:
        resp = getattr(httpx, method)(url, timeout=30.0, **kwargs)
        return resp
    except httpx.ConnectError:
        print(f"[ERROR] Cannot connect to {base}. Is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] HTTP {method.upper()} {url} failed: {e}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------
def cmd_run(args):
    """Trigger a new benchmark run via POST /api/benchmarks/run and poll progress."""
    print("=" * 60)
    print("  Chimera Cortex — RAG Benchmark CLI")
    print("=" * 60)
    print(f"  API URL     : {args.api_url}")
    print(f"  Dataset     : {args.dataset}")
    print(f"  Judge Model : {args.judge_model}")
    print(f"  Reuse Cache : {args.reuse_cache}")
    print("=" * 60)

    # Trigger the run
    resp = _api(args.api_url, "post", "/api/benchmarks/run", json={
        "dataset": args.dataset,
        "judge_model": args.judge_model,
        "reuse_cache": args.reuse_cache,
    })

    if resp.status_code == 400:
        data = resp.json()
        print(f"[WARN] {data.get('detail', 'A benchmark is already running.')}")
        sys.exit(1)
    elif resp.status_code != 200:
        print(f"[ERROR] Server returned {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    run_id = data.get("run_id")
    print(f"\n[INFO] Benchmark started — Run ID: {run_id}")
    print(f"[INFO] Polling for progress every {POLL_INTERVAL}s (Ctrl+C to detach)...\n")

    # Poll for progress
    last_completed = 0
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # Check manager status
            status_resp = _api(args.api_url, "get", "/api/benchmarks/status")
            if status_resp.status_code != 200:
                continue
            status_data = status_resp.json()

            # Also get run details to count completed questions
            run_resp = _api(args.api_url, "get", f"/api/benchmarks/{run_id}")
            if run_resp.status_code == 200:
                run_data = run_resp.json()
                total_q = run_data.get("total_questions", 0)
                completed = len(run_data.get("results", []))
                run_status = run_data.get("status", "unknown")

                if completed != last_completed:
                    pct = (completed / total_q * 100) if total_q else 0
                    bar_len = 30
                    filled = int(bar_len * completed / total_q) if total_q else 0
                    bar = "█" * filled + "░" * (bar_len - filled)
                    print(f"  [{bar}] {completed}/{total_q} ({pct:.0f}%)  status={run_status}")
                    last_completed = completed

                # Check terminal states
                if run_status in ("completed", "failed", "cancelled"):
                    print(f"\n{'=' * 60}")
                    print(f"  Benchmark finished — status: {run_status}")
                    if run_status == "completed":
                        print(f"  Avg Correctness  : {run_data.get('avg_correctness', 0):.2f}")
                        print(f"  Avg Faithfulness : {run_data.get('avg_faithfulness', 0):.2f}")
                        print(f"  Avg Relevance    : {run_data.get('avg_relevance', 0):.2f}")
                        print(f"  Pass Rate        : {run_data.get('pass_rate', 0):.1f}%")
                        print(f"  Duration         : {run_data.get('duration_seconds', 0):.1f}s")
                    print(f"  Details          : {args.api_url}/api/benchmarks/{run_id}")
                    print(f"{'=' * 60}")
                    return

            # If manager says idle but we haven't seen a terminal state, check once more
            if status_data.get("status") == "idle":
                # Give a short grace period for DB writes to settle
                time.sleep(1)
                final_resp = _api(args.api_url, "get", f"/api/benchmarks/{run_id}")
                if final_resp.status_code == 200:
                    final = final_resp.json()
                    final_status = final.get("status", "unknown")
                    if final_status != "running":
                        print(f"\n[INFO] Run ended — status: {final_status}")
                        return

    except KeyboardInterrupt:
        print("\n\n[INFO] Detached from progress polling. The benchmark continues on the server.")
        print(f"[INFO] Re-attach:  python benchmark.py status")
        print(f"[INFO] Stop it:    python benchmark.py stop")
        print(f"[INFO] Results:    {args.api_url}/api/benchmarks/{run_id}")


def cmd_status(args):
    """Show the current benchmark status."""
    resp = _api(args.api_url, "get", "/api/benchmarks/status")
    data = resp.json()
    status = data.get("status", "unknown")
    run_id = data.get("run_id")

    print(f"Benchmark Status: {status}")
    if run_id:
        print(f"Active Run ID   : {run_id}")
        # Fetch run details
        run_resp = _api(args.api_url, "get", f"/api/benchmarks/{run_id}")
        if run_resp.status_code == 200:
            run = run_resp.json()
            total = run.get("total_questions", 0)
            done = len(run.get("results", []))
            pct = (done / total * 100) if total else 0
            print(f"Progress        : {done}/{total} ({pct:.0f}%)")
    else:
        print("No benchmark is currently running.")


def cmd_list(args):
    """List all historical benchmark runs."""
    resp = _api(args.api_url, "get", "/api/benchmarks")
    data = resp.json()
    runs = data.get("runs", [])

    if not runs:
        print("No benchmark runs found.")
        return

    print(f"{'ID':>5}  {'Status':<12}  {'Dataset':<28}  {'Judge':<16}  {'Score':>6}  {'Pass%':>6}  {'Created'}")
    print("-" * 100)
    for r in runs:
        score = r.get("avg_correctness", 0)
        pr = r.get("pass_rate", 0)
        print(
            f"{r['id']:>5}  {r.get('status','?'):<12}  "
            f"{r.get('dataset_name','?'):<28}  {r.get('judge_model','?'):<16}  "
            f"{score:>5.2f}  {pr:>5.1f}  {r.get('created_at','?')}"
        )


def cmd_stop(args):
    """Cancel the currently running benchmark."""
    resp = _api(args.api_url, "post", "/api/benchmarks/stop")
    data = resp.json()
    print(data.get("message", "Done."))


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Chimera Cortex — RAG Benchmark CLI (API Client)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
sub-commands (positional):
  run       Start a new benchmark run (default when no sub-command given)
  status    Show current benchmark progress
  list      List all historical runs
  stop      Cancel the active run
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "status", "list", "stop"],
        help="Sub-command to execute (default: run)",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Base URL of the RAG API (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--dataset",
        default="benchmark_dataset.json",
        help="Path to benchmark dataset JSON (default: benchmark_dataset.json)",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"Ollama judge model (default: {DEFAULT_JUDGE_MODEL})",
    )
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        default=False,
        help="Skip cache flush and reuse existing Redis cache",
    )
    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "status": cmd_status,
        "list": cmd_list,
        "stop": cmd_stop,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
