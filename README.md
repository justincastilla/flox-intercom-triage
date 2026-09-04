# Intercom pre-triage

Reads an incoming Intercom ticket before a teammate picks it up, searches closed
tickets and the Flox docs, and leaves an internal note with a triage brief.

Notes are internal-only. The service has no tool that can message a customer.

## How it works

```
conversation.user.created  ──▶  POST /webhooks/intercom
                                  │  verify X-Hub-Signature, dedupe, return 200
                                  ▼
                                background task
                                  │  agent loop (Claude Opus 5 + 5 tools)
                                  │    search_past_tickets / read_ticket
                                  │    search_docs / read_doc
                                  │    submit_brief  ← strict schema, ends the loop
                                  ▼
                        confidence ≥ MIN_CONFIDENCE and POST_NOTES=true
                                  │
                                  ▼
                       POST /conversations/{id}/reply  (message_type: note)
```

Every run appends to `briefs.jsonl` whether or not a note was posted, so shadow
mode produces a gradeable record.

## Setup

```bash
cp .env.example .env   # then fill it in — never commit .env
```

`DOC_ROOTS` is a colon-separated list of local checkouts (docs site, repos).
Retrieval is ripgrep over those paths — keep them cloned and pulled.

```bash
flox activate
serve
```

`flox activate` builds the venv, installs deps, and sources `.env`. `serve` runs
uvicorn on `:8000`.

Expose it for webhook delivery:

```bash
cloudflared tunnel --url http://localhost:8000
```

Then in Developer Hub → your app → Configure → Webhooks, point
`conversation.user.created` at `https://<tunnel>/webhooks/intercom`.

### Required scopes

Read conversations · Write conversations · Read admins · Read articles.
Notes post as the workspace Operator bot, resolved at startup from `GET /admins`.

## Shadow mode

`POST_NOTES=false` is the default. Run it against real traffic for a week and
grade `briefs.jsonl` before letting it write anything. You can also trigger one
conversation by hand — this never posts:

```bash
curl -X POST localhost:8000/triage/<conversation_id> | jq
```

## Known limits

**Retrieval is keyword-only.** Intercom's `source.body` filter matches whole
words against a conversation's *first message* and cannot see replies. The agent
compensates by issuing several keyword searches and reading full transcripts, but
recall on paraphrased problems will be mediocre. If grading shows the agent
missing tickets a human would have found, that is the signal to build the vector
index — not before.

**Messenger flows arrive in two beats.** A conversation opened through a Messenger
workflow is created with a category button label ("Question about Flox for my
company..."), a bot asks for detail, and the customer's real question lands as a
reply — measured at 186 seconds on a real ticket. So the service triggers on
`conversation.user.replied` as well as `conversation.user.created`, skips any
conversation whose opening is a known intake label with no customer reply yet, and
claims the conversation for dedupe only at the moment it actually triages.

Intake labels are learned, not hardcoded: `app/intake.py` samples closed tickets and
treats an opening line seen verbatim in two or more conversations (and not ending in
"?") as a button label. Cached in `intake_labels.json`, refreshed weekly. Delete the
file to relearn after changing the Messenger flow.

**The model never writes URLs.** It reports what its tools returned — a local file
path with a line number, or a conversation id — and `app/links.py` maps that to a
public link deterministically. A fabricated link is the worst kind of error in a
note: it looks authoritative and costs a click to disprove. Adding a new source
root means adding a rule to `SOURCE_RULES`, not asking the model for a URL.

**Notes accept very little HTML.** A conversation note keeps only `b`, `i`, `br`,
`p`, `ul`, `li`, and `a`. `<hr>`, headings and `blockquote` are silently stripped — the
allowed-HTML list published for Help Center *Articles* does not apply here. Section
rules in `app/render.py` are therefore drawn with text, not markup. Verify any new
tag by posting once and reading the body back **without** `display_as=plaintext`,
which otherwise strips everything and makes the check meaningless.

**Dedupe keys on customer content, not conversation id.** `_claim()` stores a hash
of everything the customer has said. If a brief is produced before their real
question lands — a Messenger button label the intake detector failed to recognise,
say — the question changes the fingerprint and the conversation becomes eligible for
a fresh brief. Keying on id alone means one premature run silently blocks the real
one, which is exactly what happened on the first live webhook test.

Intake labels are matched prefix-tolerantly in both directions, because the same
Messenger button reaches the API as both "Question about Flox for my company" and
"Question about Flox for my company (paid customers, pricing, enterprise)".

**All in-process state assumes ONE machine.** `_briefed` (dedupe) and
`app/limits.py` (concurrency, cooldown, per-conversation and hourly caps) are
in-memory. On N machines the spend caps are effectively N times larger and a
webhook retry landing on a different machine can produce a duplicate brief.
`min_machines_running` is a floor, not a ceiling — check `flyctl machines list`
and keep `flyctl scale count 1` until this state moves to Postgres.

**Ticket content is untrusted.** The system prompt instructs the model to treat
ticket, doc, and past-ticket text as data, and to report embedded instructions in
`handling_notes` rather than follow them. The blast radius is small — the agent's
only write is an internal note — but read `handling_notes` when it is non-empty.

**Cost.** One brief is several Opus 5 calls with adaptive thinking. Set
`TRIAGE_EFFORT=medium` if per-ticket cost matters more than brief quality; measure
before you lower it.

## Layout

| File | What it does |
|---|---|
| `app/main.py` | FastAPI app, webhook verification, dedupe, background dispatch |
| `app/triage.py` | System prompt, tool definitions, `Brief` schema, agent loop |
| `app/intercom.py` | REST client, signature check, HTML stripping, transcripts |
| `app/docs.py` | ripgrep search + guarded file reads |
| `app/render.py` | `Brief` → the HTML Intercom accepts in a note |

## Two gates, not one

A note is posted only when `confidence >= MIN_CONFIDENCE` **and** `worth_posting`
is true. They measure different things and you need both:

- `confidence` — is this brief *correct*?
- `worth_posting` — would this brief *earn* the teammate's attention?

Spam and misdirected tickets score high on the first (the agent is certain it is
spam) and that is exactly the case where a single gate posts noise. Set
`REQUIRE_WORTH_POSTING=false` if you would rather see a note on everything.
