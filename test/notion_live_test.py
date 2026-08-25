#!/usr/bin/env python3
"""The measurement behind the Notion backend's capability flags.

`references/adapter-contract.md` says an adapter declaring `atomicAppend` without a
server-side append is telling "the most damaging lie an adapter can tell". The
paragraph in the Notion docs is not the proof — this file is.

**It measures the protocol's own shape, not a scenario the protocol never creates.**
`Sync.log_id()` says it plainly: *this run's OWN shard, one writer per document,
always* — the whole sharded design exists because Outline's append was not atomic.
So the cases below are:

1. `tree.ensure` called twice returns one page;
2. two PROCESSES append 100 lines each to ONE page whose id they are given, and the
   page ends with 200 in an order two independent reads agree on — the flag itself;
3. two processes each create their OWN shard, and `log_shards` returns BOTH the moment
   they exist. This is the Outline defect that cost eight processes eight winners: its
   search index did not return a fresh document, so each run replayed only its own log
   and concluded it had won. Notion's search is eventually consistent too, which is why
   the adapter enumerates by structure — and why that is measured here rather than
   asserted;
4. forcing `atomicAppend: false` makes the coordinator refuse lease authority.

Case 2 passes the page id rather than the title on purpose. An earlier draft had each
writer call `tree.ensure` on a shared title, and the two check-then-create calls raced
into two pages with the same name — 100 lines on one and 100 on the other, both writers
exit 0. That is a real race, but it is a race this protocol never runs into, and a
measurement that spends its evidence on an impossible scenario proves nothing about the
possible one.

Needs credentials and a network, so it is deliberately NOT part of `npm test`: CI has no
Notion workspace, and a check that cannot run there must say so rather than pass quietly.

    set -a && . ./.env.agent-sync && set +a
    python3 test/notion_live_test.py

Every page it creates is moved to the trash on the way out, including on failure.
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
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def writer(tag: str) -> int:
    """One of the two processes.

    Appends to the page id it is GIVEN when there is one — case 2, where contention on a
    single page is the point — and otherwise ensures its own shard, which is case 3 and
    what a real run does.
    """
    mod = _adapter()
    ad = mod.NotionAdapter()
    oid = os.environ.get("AGENT_SYNC_LIVE_PAGE_ID") or ad.tree_ensure(
        os.environ["AGENT_SYNC_LIVE_SHARD_PREFIX"] + tag)
    for i in range(LINES):
        ad.log_append(oid, f"- `{tag}` line {i:03d}")
    return 0


def _spawn(tags, env):
    """Two real processes, and the whole failure text when one of them dies.

    The first version truncated a writer's traceback at 400 characters, which cut it off
    at the frame header — so the one run that did crash reported a stack with no
    exception in it. A failure report that loses the failure is worse than none.
    """
    procs = [(t, subprocess.Popen([sys.executable, __file__, "--writer", t],
                                  env={**os.environ, **env},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE))
             for t in tags]
    out = []
    for tag, p in procs:
        _, err = p.communicate()
        if p.returncode != 0:
            out.append(f"writer {tag} exited {p.returncode}:\n{err.decode()[-2000:]}")
    return out


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
    stamp = int(time.time())
    shared_title = f"99 Live measurement {stamp}"
    shard_prefix = f"98 Live shard {stamp} — "
    failures: list[str] = []
    created: list[str] = []

    try:
        # 1. tree.ensure is idempotent
        first = ad.tree_ensure(shared_title)
        created.append(first)
        ad._ids.clear()                              # force the lookup, not the memo
        second = ad.tree_ensure(shared_title)
        if first == second:
            print(f"  ok  tree.ensure is idempotent — one page for two calls ({first})")
        else:
            created.append(second)
            failures.append(f"tree.ensure created two pages: {first} != {second}")

        # 2. the flag itself: two processes, one page, no lost appends
        t0 = time.time()
        crashed = _spawn(("A", "B"), {"AGENT_SYNC_LIVE_PAGE_ID": first})
        failures.extend(crashed)
        elapsed = time.time() - t0

        ad._ids.clear()
        read_one = ad.log_read(first).splitlines()
        read_two = ad.log_read(first).splitlines()
        got = len([ln for ln in read_one if ln.strip()])
        want = LINES * 2
        if crashed:
            failures.append(f"the atomicity measurement is INCONCLUSIVE: a writer died, "
                            f"so {got} lines proves nothing about whether appends are lost")
        elif got == want:
            print(f"  ok  {want} appends from two processes, {want} lines on one page "
                  f"({elapsed:.0f}s)")
        else:
            failures.append(f"atomicAppend is FALSE: {want} appends left {got} lines — "
                            "set the flag to false and stop claiming lease authority")
        if read_one == read_two:
            print("  ok  two reads return one order — totalOrderRead holds")
        else:
            failures.append("totalOrderRead is FALSE: two reads disagreed on the order")

        # 3. the Outline defect, measured rather than assumed
        crashed = _spawn(("A", "B"), {"AGENT_SYNC_LIVE_SHARD_PREFIX": shard_prefix})
        failures.extend(crashed)
        ad._ids.clear()
        shards = ad.log_shards(shard_prefix)
        created.extend(s for s in shards if s not in created)
        if crashed:
            failures.append("the shard measurement is INCONCLUSIVE: a writer died")
        elif len(shards) == 2:
            total = sum(len([l for l in ad.log_read(s).splitlines() if l.strip()])
                        for s in shards)
            if total == LINES * 2:
                print(f"  ok  both fresh shards enumerated, {total} lines across them — "
                      "a run cannot mistake its own shard for the whole log")
            else:
                failures.append(f"the two shards hold {total} lines, expected {LINES * 2}")
        else:
            failures.append(f"log_shards returned {len(shards)} of 2 shards moments after "
                            "they were created — a run that cannot see the other shard "
                            "replays only its own and concludes it won")

        # 4. the degradation path, forced rather than described
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
        for oid in created:
            try:
                # `in_trash`, not `archived`: under Notion-Version 2026-03-11 the old
                # field is rejected outright — "body.archived should be not present".
                # Measured 2026-08-25, when the cleanup failed and the first measurement
                # page had to be trashed by hand.
                ad._call("PATCH", f"pages/{oid}", {"in_trash": True})
            except Exception as exc:             # cleanup must never mask the result
                print(f"  !!  could not trash {oid}: {exc}", file=sys.stderr)
        if created:
            print(f"  ok  {len(created)} measurement page(s) trashed")

    if failures:
        print()
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("\nPASS: notion live measurement — the capability flags are earned")
    return 0


if __name__ == "__main__":
    if "--writer" in sys.argv:
        sys.exit(writer(sys.argv[sys.argv.index("--writer") + 1]))
    sys.exit(main())
