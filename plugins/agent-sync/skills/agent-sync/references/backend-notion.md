# Notion backend

**Read this when** making any Notion API call or debugging one.

## Contents

- [Shape of the API](#shape-of-the-api)
- [Capabilities](#capabilities)
- [Primitive mapping](#primitive-mapping)
- [Why generated pages are one code block](#why-generated-pages-are-one-code-block)
- [Calling it without leaking the token](#calling-it-without-leaking-the-token)
- [Rate limits](#rate-limits)
- [Getting a token](#getting-a-token)
- [Sharing the plane with another person](#sharing-the-plane-with-another-person)
- [Verifying access](#verifying-access)


[Notion](https://www.notion.so) is single-tenant SaaS. There is no instance URL to
configure and none to leak: every client talks to the same host, which is why
`api.notion.com` is a constant in this code and Outline's address is not.

## Shape of the API

- Base: `https://api.notion.com/v1`. Methods are the ordinary ones — `GET` reads,
  `POST` creates, `PATCH` appends and updates, `DELETE` removes.
- `Authorization: Bearer <token>`, `Content-Type: application/json`, and
  **`Notion-Version` is required**. This adapter pins `2026-03-11`; an unpinned client
  gets whatever is current on the morning a breaking change ships.
- Errors are unenveloped: the HTTP status is the status, and the body carries
  `{"code": "...", "message": "..."}`. The codes that matter here are
  `rate_limited` (429), `service_overload` (529) and **`conflict_error` (409)**.

## Capabilities

```json
{ "atomicAppend": true, "totalOrderRead": true, "search": true }
```

`atomicAppend` is true because `PATCH /v1/blocks/{id}/children` is documented as
"creates and appends new children blocks to the parent block_id specified", appended
"to the end of the parent block's children" by default. There is no read-modify-write
anywhere in the adapter, which is the whole of what the flag claims.

**`exclusiveLease` is false, and no measurement can move it.** Notion has no
compare-and-swap: nothing in the API lets a writer say *only if the page is still at
the revision I read*. The endpoint documents `conflict_error` for two writers
colliding, which is a retry hint, not an arbiter. Exclusion stays with `leaseBackend`
— see `lease-protocol.md`.

**These flags come from a measurement, not from the paragraph above.**
`test/notion_live_test.py` is that measurement, and it passed against a live workspace on
2026-08-25: 200 appends from two processes left 200 lines on one page, two independent
reads returned one order, and two freshly created shards were both enumerated. Re-run it
after any change to this adapter. The equivalent trap on Outline returned twelve successes
for twelve concurrent appends and left three lines, which is why this is measured rather
than read off the endpoint's documentation.

**One thing the measurement is not allowed to test**, because the protocol never does it:
two runs calling `tree.ensure` on the SAME title at once. The check-then-create races and
both win a page of that name — observed, 100 lines on one and 100 on the other, both
writers exiting 0. `Sync.log_id()` gives every run its own shard for exactly this class of
reason, so the contention case is handed a page id instead.

## Primitive mapping

| Primitive | Call | Body |
|---|---|---|
| `tree.ensure` (container) | `POST /v1/pages` | `{parent: {page_id}, properties: {title}}` — `bootstrap` creates it once |
| `tree.ensure` (document) | list children, else `POST /v1/pages` | matched on `child_page.title`, never on search |
| `log.append` | `PATCH /v1/blocks/{id}/children` | one `paragraph` block per line |
| `log.read` | `GET /v1/blocks/{id}/children` | cursor-paginated; join each block's text with a newline |
| `doc.put` | `PATCH /v1/blocks/{block_id}` | one `code` block, replaced wholesale |
| `doc.get` | `GET /v1/blocks/{id}/children` | the `code` block's text |
| `search` | `POST /v1/search` | `{query, page_size}` |

**Shards are enumerated by structure, never by search.** Notion's search index is
eventually consistent, so a page it has not indexed yet reads as a page that does not
exist — and a run that cannot see the other shards replays only its own and concludes
it won. Outline paid for this lesson with eight processes and eight winners; the
listing endpoint returns a page the moment it is created.

Two hard limits: **100 children per append request**, and **2000 characters per
rich-text item**. Lines are chunked below the second, at 1900.

## Why generated pages are one code block

The obvious `doc.put` — delete every child, append the new ones — costs one request
per existing block. At three requests a second a regenerated board takes minutes, and
the board is regenerated often. A single `code` block is two requests whatever the
size, and it also stops the Notion editor from reinterpreting generated markdown as
Notion formatting.

## Calling it without leaking the token

`curl -H "Authorization: Bearer $TOKEN"` puts the credential in `argv`, where every
other process on the machine can read it. Use a config file on stdin — `--config -`
reads the whole request description from a heredoc, so neither the token nor the
payload appears in the process table.

**Prefer the bundled `scripts/agent_sync.py`.** It calls the API through `urllib`
inside its own process — no subprocess, no `argv`, nothing for another process to read.

## Rate limits

Two of them, and the second is the one that surprises people:

- **Per connection** — an average of three requests per second, with some burst.
- **Per workspace** — shared across every connection in it, scaled to the plan.

Either returns `rate_limited` / 429 with `additional_data.rate_limit_reason` naming
which one; 529 `service_overload` means Notion itself is having a minute. Both carry
`Retry-After` in integer seconds. Honour it, back off exponentially, at most five
attempts, then fail loudly. Do not spin.

Because the second limit is shared, **a rate limit here is not necessarily your own
traffic** — another script or another person in the same workspace can spend it. The
adapter says so in its message rather than blaming the caller.

## Getting a token

A personal access token from Notion's developer portal — Personal access tokens →
New token. It begins `ntn_`. No integration and no page-sharing dance is required:
the token acts as the person who made it.

```
AGENT_SYNC_NOTION_TOKEN=<created by the operator, pasted by the operator>
AGENT_SYNC_NOTION_PARENT=<the 32-character id ending the parent page's URL>
AGENT_SYNC_NOTION_COLLECTION=<container page id, printed by `bootstrap`>
```

The operator creates it and pastes it themselves. Do not ask for the value in chat,
do not read it back, and do not write it anywhere in the repository.

## Sharing the plane with another person

This is the reason the backend exists. Each person uses **their own** token; what
they share is the container page. Share it in Notion the ordinary way, and every
holder of a token that can see that page reads the same plane.

What that does **not** give you is a shared lease: the plane carries the record and
the awareness, and `leaseBackend` decides exclusion. Two people on two machines want
`leaseBackend: "git"`, whose non-fast-forward rejection is a real compare-and-swap.

## Verifying access

```bash
python3 scripts/agent_sync.py check
```

The preflight calls `GET /v1/users/me` — one request, no writes — and then resolves
the container id. A `401` means the token is wrong or revoked, and retrying with the
same one will not help. An `object_not_found` on the container means the token cannot
see that page: share the page with the person whose token it is.
