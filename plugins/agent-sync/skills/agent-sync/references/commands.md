# The commands

Every verb of `python3 "$SKILL_DIR/scripts/agent_sync.py" <verb>`. `--help` on any verb
prints its flags. Moved out of `SKILL.md` to keep the skill body under its token budget.

| Command | Does |
|---|---|
| `init` | **Run first.** Ask where state lives, write config + gitignored env file, print the operator's step |
| `status` | Inspect, repair, report, name one next action |
| `bootstrap` | Create the cloud container and print the id to paste into the env file |
| `acquire <KEY>` | Take the lease on a task id. Prints `won` or `lost <holder>` |
| `renew <KEY>` | Explicit keepalive. The hook uses `--active-only`; unrelated calls do not prolong claims |
| `release <KEY>` | Give the lease back on every path; `released` means the remote ref is gone |
| `release --held` | Give back everything this run holds, in one process: what SessionEnd runs |
| `reserve <REG> [--key K] [--offline]` | Reserve the next id in a register (`DEC`, `OQ`, `DEP`, …); prints it. `--key` makes a retry idempotent (one key, one number); `--offline` issues a namespaced `REG-o-…` id with no global authority |
| `map-offline <REG> <ID> <N>` | Bind an offline id to a properly reserved number: append-only, never rebound |
| `release-id <REG> <ID>` | Return an id you did not end up writing to git |
| `journal <text>` | Append one line to this run's journal |
| `record <text>` | Append what you **actually built**: `--decision DEC-…`, `--files a,b` |
| `reconcile` | Intent (git) vs as-built (cloud). `--set-baseline` once per project |
| `signal <DEP-ID> <state>` | Record dependency state, never an ask. With Fabric, ask via `com.submit`; the DEP cites its board message |
| `guard <path>` | Answer whether this run may write that path. Exit 0 = yes, 2 = no |
| `board` | Regenerate the shared board and this repo's page. `--mirror` also renders the configured git docs into the plane |
| `whoami` | Print this run's id and its held leases |
| `residue` | What a run left behind in BOTH planes: expired locks on disk, and claim tags with no live lease behind them |
| `reap [KEY…]` | Clear only what this run **proves** it owns and has spent, then re-read the directory to confirm it went |
| `reap --long-expired` | List locks expired 7+ days on both planes, each with its verdict. `--apply` clears them by age alone: **the operator's call, never yours**, unless the host delegates (SKILL.md, *Hosts that delegate*) |
| `setup` | Write the generated snapshot of how **this** project is wired, for agents to read |
| `adopt` | Inspect an existing project and **propose** a config: writes nothing |
| `merge` | Land this branch: conflicts checked **before** anything is touched, merge log written, the `--key` lease released after the push. `--summary`, `--dry-run`, `--push` |
| `merges` | What landed while you were on your branch. `--all` includes the compacted tail |
| `check` | Validate the whole setup end to end. Non-zero when it is not healthy |
| `scaffold [--full]` | Create only what is missing, never a line over what exists. `--full` also seeds the question register, index, dependency board, data model and docs gate |
| `finish [--gates]` | Is the **work** finished: every repository clean, pushed and pointed at, nothing left held. `check` answers whether the project is wired right; this, whether you are done |
