# Outline backend

**Read this when** making any Outline API call or debugging one.

## Contents

- [Shape of the API](#shape-of-the-api)
- [Capabilities](#capabilities)
- [Primitive mapping](#primitive-mapping)
- [Calling it without leaking the token](#calling-it-without-leaking-the-token)
- [Rate limits](#rate-limits)
- [Getting a token](#getting-a-token)
- [Verifying an instance](#verifying-an-instance)


[Outline](https://www.getoutline.com) is a collaborative knowledge base, available
hosted or self-hosted. Both work; the instance URL is configuration, never code.

## Shape of the API

- Base: `<AGENT_SYNC_OUTLINE_URL>/api` — the instance URL comes from the
  environment. **Never hardcode a host, not even in a test or an example.**
- **Every endpoint is `POST`**, including reads.
- `Authorization: Bearer <token>` and `Content-Type: application/json`.
- Every response is enveloped: `{"ok": true, "status": 200, "data": {…}}` or
  `{"ok": false, "error": "…", "message": "…", "status": 4xx}`. Check `ok`; an
  HTTP 200 with `ok: false` is a failure.

## Capabilities

```json
{ "atomicAppend": true, "totalOrderRead": true, "search": true }
```

`atomicAppend` is true because `documents.update` accepts
`editMode: "append"`, which appends server-side without a read-modify-write cycle.

**There is no `lastRevision` parameter and no other optimistic-concurrency
control.** This is the single most important fact about this backend: a `replace`
update is last-write-wins and will silently discard a concurrent edit. Coordination
state is therefore never modelled as a document you rewrite — see
`lease-protocol.md`.

## Primitive mapping

| Primitive | Call | Body |
|---|---|---|
| `tree.ensure` (container) | `POST /api/collections.create` | `{name}` — or `collections.list` first and reuse |
| `tree.ensure` (document) | `POST /api/documents.create` | `{collectionId, parentDocumentId?, title, text, publish: true}` |
| `log.append` | `POST /api/documents.update` | `{id, text: "<line>\n", editMode: "append"}` |
| `log.read` | `POST /api/documents.info` | `{id}` → `data.text` |
| `doc.put` | `POST /api/documents.update` | `{id, text, editMode: "replace"}` |
| `doc.get` | `POST /api/documents.info` | `{id}` → `data.text` |
| `search` | `POST /api/documents.search` | `{query, limit, collectionId?}` |

`publish: true` on create, or the document stays a draft and no other agent can
read it.

## Calling it without leaking the token

`curl -H "Authorization: Bearer $TOKEN"` puts the credential in `argv`, where every
other process on the machine can read it. Use a config file on stdin instead:

```bash
payload=$(mktemp); chmod 600 "$payload"
printf '%s' "$body_json" > "$payload"

curl -sS --config - <<EOF
url = "$AGENT_SYNC_OUTLINE_URL/api/documents.update"
request = "POST"
header = "Authorization: Bearer $AGENT_SYNC_OUTLINE_TOKEN"
header = "Content-Type: application/json"
data-binary = "@$payload"
EOF

rm -f "$payload"
```

The heredoc reaches curl on stdin, so neither the token nor the payload appears in
the process table. Delete the payload file on every exit path.

**Prefer the bundled `scripts/agent_sync.py` over hand-rolling any request.** It
calls the API through `urllib` inside its own process — no subprocess, no `argv`,
nothing for another process to read. The `curl` recipe above is for the case where
you must issue a call by hand; it is the safe way to do that, not the better way.

## Rate limits

`429` comes back with `Retry-After` (seconds) plus `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset`. Honour `Retry-After`, then back off
exponentially, at most 5 attempts. Do not spin.

## Getting a token

The operator creates an API key in their own instance's settings and puts it in
their environment. Do not ask for the value in chat, do not read it back, and do not
write it anywhere in the repository.

```
AGENT_SYNC_OUTLINE_URL=https://<your-instance>
AGENT_SYNC_OUTLINE_TOKEN=<created by the operator>
AGENT_SYNC_OUTLINE_COLLECTION=<collection id, printed by `init`>
```

## Verifying an instance

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  "$AGENT_SYNC_OUTLINE_URL/api/auth.info" -H 'Content-Type: application/json' -d '{}'
```

`401` is the healthy answer without a token: the host is up and demands auth.
A connection error means the URL is wrong or the instance is down — that is not a
credentials problem, and retrying with a different token will not help.
