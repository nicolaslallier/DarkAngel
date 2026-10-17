# Chat

`/chat` is a Claude-style chat page: a conversation sidebar, streamed
markdown replies, model picker, stop, regenerate and edit-and-resend.
Conversations and messages live in PostgreSQL (`conversations`, `messages`,
every query filtered on the caller's `sub`). The model is reached through a
LiteLLM proxy; the SPA never talks to it.

## Settings

Backend environment (`DARKANGEL_` prefix, see `core/config.py`):

| Variable | Meaning |
|---|---|
| `DARKANGEL_LLM_URL` | LiteLLM base URL, e.g. `http://192.168.2.35:4000`. Empty (default) disables chat: every `/api/chat` route answers 503. |
| `DARKANGEL_LLM_API_KEY` | LiteLLM key. A Portainer stack variable, never in git. Today it is the LiteLLM master key, since LiteLLM runs without a database. |
| `DARKANGEL_EXO_URL` | Exo cluster, e.g. `http://192.168.2.35:52415`; lists the models Exo has loaded. |
| `DARKANGEL_LLM_TIMEOUT_SECONDS` | Per-request timeout, default 300. |

## Prerequisite: LiteLLM `model_list`

A model Exo has loaded is only usable if it is also in LiteLLM's
`model_list`. Today only `GLM-4.7-Flash` is listed while Exo has
`Qwen3.6-35B-A3B` loaded: add the loaded model(s) to LiteLLM first.

## How a reply runs

`POST .../messages` (or `/regenerate`) starts the reply as an asyncio task in
the API process (`chat/runs.py`) and streams it back as server-sent events.
The task, not the HTTP connection, owns the reply: closing the tab or
navigating away never cancels it; only `POST .../stop` does. The task saves
the assistant message as it finishes (or as `stopped` / error), then sends
`done` and leaves the registry. Only after that, on a conversation's first
reply, a detached task asks the same model for a short title (best effort, up
to 120 s; titles take ~70 s on this cluster) and saves it as a rename; the
sidebar shows it on its next refresh. Stop has no effect on it.

Reattach: when the SPA reopens a conversation that is still running it calls
`GET .../stream`, which replays what has been generated so far and then
follows live (204 if nothing is running).

## Ceiling: one worker

Run registry state is in process memory, so the API must run **one uvicorn
worker and one replica**; a second process could not reattach to or stop a run
it does not hold. A redeploy interrupts any reply in flight: the startup sweep
saves it as `stopped`.

## Rendering and safety

Model text becomes HTML only through `frontend/src/markdown.ts`: markdown is
rendered, then sanitised with a DOMPurify tag/attribute allowlist. Images are
rendered as links, so no remote resource loads automatically.

## Deployment

`deploy/portainer-stack.yml` sets the two URLs; `LLM_API_KEY` is a Portainer
stack variable, set in `.portainer.env` and handed over by `make up` (rerun it
after a change). The CI deploy (`.github/workflows/deploy.yml`) rewrites the
stack's whole Env list, so it needs the same value as the `LLM_API_KEY`
repository secret. Owner prerequisites: add the Exo-loaded model(s) to LiteLLM's
`model_list`, and rotate the LiteLLM master key (it was exposed during design),
then update `LLM_API_KEY` (in `.portainer.env` and the repository secret) and
redeploy.
