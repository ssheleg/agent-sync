# Filesystem backend — the degraded mode

**Read this when** running without a cloud backend, or explaining to an operator
what they do and do not get.

## What it is

Plain files under `.agent-sync/` in the repository:

```
.agent-sync/
├── claims.log          # the append log
├── reservations.log
├── signals.log
├── runs/<runId>.log
└── board.md            # generated
```

## Capabilities — declared honestly

```json
{ "atomicAppend": false, "totalOrderRead": false, "search": false }
```

`atomicAppend` is false: agents on two clones append to two files, and a merge decides the
order after the fact — not at the moment the protocol needs one.

## What follows from that

**No adapter is ever the lease authority** — not this one, not the cloud. Exclusion comes
from `leaseBackend` (`local` = `O_EXCL`, `git` = a pushed ref). What this backend costs is
**awareness**: with it configured, the coordinator:

1. says so at session start, in one plain sentence;
2. keeps the lease exactly as configured — `leaseBackend` is independent of this choice;
3. marks every run `ungated` on the board, because nobody else can read the state.
   In the five-field contract (`adapter-contract.md` → *The status capability
   contract*, the one source this file describes the mode from) that is
   `awareness_scope: isolated`, `enforcement_mode: advisory` — and if the local
   state cannot even be read, `backend_health: failed`, reported as `unknown`,
   never as active green.

## The lease is not this backend's job

Do not look for a lease mechanism here. `leaseBackend: "local"` decides with an atomic
file create; `leaseBackend: "git"` decides with a pushed ref whose non-fast-forward
rejection is a real compare-and-swap. Both work regardless of which knowledge backend is
configured — see `lease-protocol.md`.

## When this is the right choice

- A project with one agent at a time, which wants the journal and the board without
  standing up a service.
- An air-gapped or offline repository.
- A first run, before the operator has chosen a knowledge backend.

## When it is the wrong choice

- Several agents at once: they hold leases correctly but cannot **see** each other, and
  the whole point of the coordination plane is that they can.
- Anything where the operator has been told the project is coordinated. The lease still
  works; the awareness does not, and the board must keep saying `ungated`.
