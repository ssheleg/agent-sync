> **`settleSeconds` is this contract's extension point, and nothing shipped reads it.**
> It exists for an adapter whose writes are not immediately visible to a subsequent read —
> a store with an asynchronous index, where a reader must wait before replaying. Neither
> `outline` nor `fs` needs it, so `check` says plainly that setting it does nothing here.
> It is kept rather than removed because deleting it would make every config carrying one
> fail validation to buy nothing, and because an adapter that needs it has nowhere else to
> ask. If you add a backend that waits, this is the knob; if you do not, ignore it.

# Adapter contract

**Read this when** adding a knowledge backend, auditing one, or deciding whether a
candidate backend can be trusted with leases.

A backend is an adapter that implements six primitives and declares three
capabilities. Nothing else about it is the coordinator's business.

## Primitives

| Primitive | Signature | Semantics |
|---|---|---|
| `tree.ensure` | `(path) -> id` | Idempotently create the container/document at `path`, return its id. MUST NOT overwrite existing content. |
| `log.append` | `(id, line) -> ok` | Append exactly one `\n`-terminated line to the end of the object's text, **server-side**, with no read-modify-write cycle. |
| `log.read` | `(id) -> text` | Return the object's full text. Line order MUST be identical for every reader at a given revision. |
| `doc.put` | `(id, text) -> ok` | Replace the object's text wholesale. Generators only. |
| `doc.get` | `(id) -> text` | Return the object's text. |
| `search` | `(query, limit) -> [{id,title,snippet}]` | Full-text search. Feeds the pipeline's stage-0 knowledge harvest. |

## Capabilities

```json
{ "atomicAppend": true, "totalOrderRead": true, "search": true,
  "exclusiveLease": false }
```

- **`atomicAppend`** — `log.append` reaches the server as an append. If the adapter
  implements it as *read text, concatenate, write text back*, this is **false**, no
  matter how fast that is.
- **`totalOrderRead`** — concurrent appends land in one order and every reader sees
  the same order. A backend that merges concurrently, or that returns per-reader
  views, is **false**.
- **`search`** — `search` is implemented rather than stubbed.
- **`exclusiveLease`** — whether *this backend* can decide a contended lease. Almost
  always **false**: it requires compare-and-swap, and none of the document stores has
  it. Declaring it true without one is the most damaging lie an adapter can tell, so
  the default is false and the burden of proof is on the adapter.

## Degradation — non-negotiable

**No adapter is the lease authority.** Exclusion is an atomic local lock; the adapter
carries the record and the awareness. If `atomicAppend` or `totalOrderRead` is false the
coordinator additionally:

1. states it once, in plain words, at session start;
2. keeps the local lock as the only arbiter (`references/backend-fs.md`);
3. marks every run `ungated` on the board.

There is no third option. A lease that is not actually exclusive is worse than no
lease at all, because the other agent stops checking.

## Errors and retries

Every primitive returns a typed failure; none may raise past the caller.

| Condition | Handling |
|---|---|
| Rate limited | Honour the backend's own retry hint; exponential backoff; at most 5 attempts, then fail loudly |
| Auth failure | Fail immediately. Never retry a credential — it is not going to become valid |
| Not found | For `tree.ensure`, create. For everything else, report the missing path; do not create silently |
| Transport error | Retry twice with backoff, then fail with the underlying message intact |

Never swallow an error into a success. A coordination layer that reports a write it
did not make is the failure this whole design exists to prevent.

## Credentials

- Read from the environment only. Never from the config file, never from `argv`.
- Never echo, never log, never include in a journal line or a board render.
- When absent: degraded mode with a clear message, not a crash and not a prompt.

## Adding a backend — checklist

- [ ] Six primitives implemented, each returning a typed failure
- [ ] Three capabilities declared **honestly**; `atomicAppend` false unless the append is server-side
- [ ] Credentials read from env only, and absent from every code path that builds a command line
- [ ] Rate-limit hint honoured
- [ ] `tree.ensure` proven idempotent by calling it twice against a live instance
- [ ] `log.append` proven atomic: two processes appending 100 lines each yield 200 lines, in one order both readers agree on
- [ ] Degradation path exercised: force `atomicAppend: false` and confirm the coordinator refuses lease authority and says so
