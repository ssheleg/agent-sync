#!/usr/bin/env python3
"""The measurement behind the Notion backend's capability flags.

`references/adapter-contract.md` says an adapter declaring `atomicAppend` without a
server-side append is telling "the most damaging lie an adapter can tell". The
paragraph in the Notion docs is not the proof — this file is:

* `tree.ensure` called twice returns one id, against a live workspace;
* two PROCESSES append 100 lines each and the page ends with 200, in one order that
  two independent reads agree on;
* forcing `atomicAppend: false` makes the coordinator refuse lease authority.

It needs credentials and a network, so it is deliberately NOT part of `npm test`:
CI has no Notion workspace, and a check that cannot run there must say so rather
than pass quietly. Run it by hand after `bootstrap`:

    set -a && . ./.env.agent-sync && set +a
    python3 test/notion_live_test.py

Every page it creates is archived on the way out, including on failure.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
LINES = 100


def _adapter():
    sys.path.insert(0, str(SCRIPT.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def writer(tag: str) -> int:
    """One of the two processes. Appends LINES lines and says nothing else."""
    mod = _adapter()
    ad = mod.NotionAdapter()
    oid = ad.tree_ensure(os.environ["AGENT_SYNC_LIVE_PAGE"])
    for i in range(LINES):
        ad.log_append(oid, f"- `{tag}` line {i:03d}")
    return 0


def main() -> int:
    if not (os.environ.get("AGENT_SYNC_NOTION_TOKEN")
            and os.environ.get("AGENT_SYNC_NOTION_COLLECTION")):
        print("SKIP: notion live measurement — AGENT_SYNC_NOTION_TOKEN and "
              "AGENT_SYNC_NOTION_COLLECTION are not both set.")
        print("      This is a real gap, not a pass: the capability flags in "
              "NotionAdapter are unverified until this runs.")
        return 0

    mod = _adapter()
    ad = mod.NotionAdapter()
    page = f"99 Live measurement — {int(time.time())}"
    os.environ["AGENT_SYNC_LIVE_PAGE"] = page
    failures: list[str] = []
    oid = ""

    try:
        # 1. tree.ensure is idempotent
        first = ad.tree_ensure(page)
        ad._ids.clear()                      # force the lookup, not the memo
        second = ad.tree_ensure(page)
        oid = first
        if first == second:
            print(f"  ok  tree.ensure is idempotent — one page for two calls ({first})")
        else:
            failures.append(f"tree.ensure created two pages: {first} != {second}")

        # 2. log.append is atomic under two PROCESSES, not two threads
        t0 = time.time()
        procs = [subprocess.Popen([sys.executable, __file__, "--writer", tag],
                                  env={**os.environ}, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
                 for tag in ("A", "B")]
        for p in procs:
            out, err = p.communicate()
            if p.returncode != 0:
                failures.append(f"writer failed: {err.decode()[:400]}")
        elapsed = time.time() - t0

        ad._ids.clear()
        read_one = ad.log_read(oid).splitlines()
        read_two = ad.log_read(oid).splitlines()
        got = len([ln for ln in read_one if ln.strip()])
        want = LINES * 2
        if got == want:
            print(f"  ok  {want} lines from two processes, {want} lines on the page "
                  f"({elapsed:.0f}s)")
        else:
            failures.append(f"atomicAppend is FALSE: {want} appends left {got} lines — "
                            "set the flag to false and stop claiming lease authority")
        if read_one == read_two:
            print("  ok  two reads return one order — totalOrderRead holds")
        else:
            failures.append("totalOrderRead is FALSE: two reads disagreed on the order")

        # 3. the degradation path, forced rather than described
        saved = mod.NotionAdapter.capabilities
        try:
            mod.NotionAdapter.capabilities = {**saved, "atomicAppend": False}
            if mod.NotionAdapter().is_lease_authority:
                failures.append("with atomicAppend false the adapter still claims "
                                "lease authority")
            else:
                print("  ok  atomicAppend false → the adapter refuses lease authority")
        finally:
            mod.NotionAdapter.capabilities = saved

    finally:
        if oid:
            try:
                ad._call("PATCH", f"pages/{oid}", {"archived": True})
                print(f"  ok  measurement page archived ({oid})")
            except Exception as exc:            # cleanup must never mask the result
                print(f"  !!  could not archive {oid}: {exc}", file=sys.stderr)

    if failures:
        print()
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print(f"\nPASS: notion live measurement — the capability flags are earned")
    return 0


if __name__ == "__main__":
    if "--writer" in sys.argv:
        sys.exit(writer(sys.argv[sys.argv.index("--writer") + 1]))
    sys.exit(main())
