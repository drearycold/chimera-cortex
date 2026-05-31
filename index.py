#!/usr/bin/env python3
"""
Chimera Cortex — Ingestion CLI (HTTP API Wrapper)
================================================
A thin CLI client that triggers and monitors document ingestion runs through
the running web application's HTTP API. All core ingestion logic lives in
cortex/ingest.py and is executed server-side.

Usage:
    python index.py                             # Ingest default directory and poll progress
    python index.py --api-url http://10.0.0.5:8000
    python index.py --source-dir servant_lore_md_v3
    python index.py status                      # Show current ingestion status
    python index.py stop                        # Cancel the active ingestion run
"""

import argparse
import sys
import time
import httpx

from cortex.core.config import DEFAULT_API_URL

POLL_INTERVAL = 1  # seconds between status polls

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

def cmd_run(args):
    """Trigger a new ingestion run via POST /api/ingest/run and poll progress."""
    print("=" * 60)
    print("  Chimera Cortex — Document Ingestion CLI")
    print("=" * 60)
    print(f"  API URL    : {args.api_url}")
    print(f"  Source Dir : {args.source_dir}")
    print("=" * 60)

    # Trigger the run
    resp = _api(args.api_url, "post", "/api/ingest/run", json={
        "source_dir": args.source_dir,
    })

    if resp.status_code == 400:
        data = resp.json()
        print(f"[WARN] {data.get('detail', 'Ingestion is already running.')}")
        sys.exit(1)
    elif resp.status_code != 200:
        print(f"[ERROR] Server returned {resp.status_code}: {resp.text}")
        sys.exit(1)

    print("\n[INFO] Ingestion started on the server.")
    print(f"[INFO] Polling for progress every {POLL_INTERVAL}s (Ctrl+C to detach or stop)...\n")

    # Poll for progress
    last_processed = -1
    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # Check status
            status_resp = _api(args.api_url, "get", "/api/ingest/status")
            if status_resp.status_code != 200:
                continue
            status_data = status_resp.json()

            status = status_data.get("status", "unknown")
            processed = status_data.get("processed_files", 0)
            total = status_data.get("total_files", 0)
            current = status_data.get("current_file", "")
            chunks = status_data.get("total_chunks_indexed", 0)

            if total > 0 and processed != last_processed:
                pct = (processed / total * 100) if total else 0
                bar_len = 30
                filled = int(bar_len * processed / total) if total else 0
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"  [{bar}] {processed}/{total} ({pct:.0f}%)  current='{current}'  chunks={chunks}  status={status}")
                last_processed = processed

            # Check terminal states
            if status in ("completed", "failed", "cancelled"):
                print(f"\n{'=' * 60}")
                print(f"  Ingestion finished — status: {status}")
                if status == "completed":
                    print(f"  Processed Files     : {processed}")
                    print(f"  Total Chunks Indexed: {chunks}")
                elif status == "failed":
                    print(f"  Error Message       : {status_data.get('error_message')}")
                print(f"{'=' * 60}")
                return

    except KeyboardInterrupt:
        print("\n\n[INFO] Detached from progress polling. The ingestion run continues on the server.")
        print(f"[INFO] Re-attach:  python index.py status")
        print(f"[INFO] Stop it:    python index.py stop")

def cmd_status(args):
    """Show the current ingestion status."""
    resp = _api(args.api_url, "get", "/api/ingest/status")
    data = resp.json()
    status = data.get("status", "unknown")
    processed = data.get("processed_files", 0)
    total = data.get("total_files", 0)
    current = data.get("current_file", "")
    chunks = data.get("total_chunks_indexed", 0)

    print(f"Ingestion Status     : {status}")
    if status == "running":
        pct = (processed / total * 100) if total else 0
        print(f"Progress             : {processed}/{total} ({pct:.0f}%)")
        print(f"Current File         : {current}")
        print(f"Total Chunks Indexed : {chunks}")
    elif status == "completed":
        print(f"Successfully processed {processed} files.")
        print(f"Total Chunks Indexed : {chunks}")
    elif status == "failed":
        print(f"Ingestion failed: {data.get('error_message')}")

def cmd_stop(args):
    """Cancel the currently running ingestion."""
    resp = _api(args.api_url, "post", "/api/ingest/stop")
    data = resp.json()
    print(data.get("message", "Done."))

def main():
    parser = argparse.ArgumentParser(
        description="Chimera Cortex — Ingestion CLI (API Client)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
sub-commands (positional):
  run       Start a new document ingestion run (default when no sub-command given)
  status    Show current ingestion progress
  stop      Cancel the active ingestion run
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "status", "stop"],
        help="Sub-command to execute (default: run)",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Base URL of the RAG API (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--source-dir",
        default="documents",
        help="Path to source directory of markdown documents (default: documents)",
    )
    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "status": cmd_status,
        "stop": cmd_stop,
    }
    dispatch[args.command](args)

if __name__ == "__main__":
    main()
