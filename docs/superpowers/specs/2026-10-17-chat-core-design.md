# Chat with the local LLM: core chat

Date: 2026-10-17 · Status: draft, awaiting review

## Intent

The owner wants a **personal day assistant** inside DarkAngel, running on their own
models (an exo cluster on the Mac at `192.168.2.35`, behind a LiteLLM router). In
the long run it will remember things, work inside projects, read files, run
scripts ("skills") and show artifacts. This spec is the first brick only: a
**Claude-style chat for general questions**. Success: the owner opens `/chat`, picks
a model, asks something, watches the answer (and the model's thinking) stream in, and
finds the conversation again later.

It is the first of six specs, built in this order: **core chat → memory → projects →
files → skills → artifacts**. Nothing here builds the later five; nothing here may
make them hard (see *Later specs* at the end).

Non-goals for this spec: memory, projects, attachments, tool calling, artifacts,
voice, sharing a chat, a user-editable system prompt, conversation branching.

Anti-goals (failure even if it works): model-written markup able to run script in the
SPA's origin; another user (household member) ever seeing a chat; the LiteLLM key
reaching the browser or git.

## Decisions taken

By the owner:

- **Claude is the reference** for look and behaviour (mockup:
  `.superpowers/brainstorm/*/content/chat-layout.html`).
- **Chats are private** to the caller's Keycloak `sub`, like files. Not household-scoped.
- **Anyone who can sign in may chat** (`Claims` dependency, no household role, no
  allowlist). Household members share the cluster, so one person's reply can queue
  behind another's; accepted.
- **Replies keep going in the background.** Switching conversations, leaving `/chat`,
  closing the tab: the reply still finishes and is saved. Coming back shows it live
  if still running. Only the **Stop** button stops a reply.
- **The backend talks to the LiteLLM router**, not to exo:
  `http://192.168.2.35:4000/v1`, OpenAI-compatible, authenticated with the LiteLLM
  **master key** (LiteLLM has no database, so virtual keys are not possible yet).
- **Model picker like Claude, showing loaded / not loaded**, which means DarkAngel also
  reads exo directly (`http://192.168.2.35:52415/state`).

Verified against the live stack on 2026-10-17:

- LiteLLM `/v1/models` lists only `mlx-community/GLM-4.7-Flash-8bit`; exo has
  `mlx-community/Qwen3.6-35B-A3B-8bit` loaded. **Deployment prerequisite (owner):**
  add the Qwen model to LiteLLM's `model_list`. Asking for an unloaded model returns
  a 404 `No instance found for model`, then 429 `all_deployments_in_cooldown`.
- Streaming is standard SSE ending in `data: [DONE]`. Deltas carry **`content`** and,
  separately, **`reasoning_content`** (thinking). In a 400-token test, 399 tokens were
  reasoning, so **no small `max_tokens`** may be sent.
- Speed: about 7.7 tokens/s generation. A reply takes tens of seconds to minutes.
- exo also emits SSE comment lines `: prefill_progress {...}` while reading the prompt
  (not checked whether LiteLLM forwards them).
- `/model/info` (master key) maps each LiteLLM `model_name` to its upstream
  (`litellm_params.model` = `openai/<exo model id>`, `api_base` = exo).
- exo `GET /state` → `instances.{id}.<Kind>Instance.shardAssignments.modelId` = the
  loaded model ids.
- Tool calling works (correct OpenAI `tool_calls`, `finish_reason: tool_calls`). It
  matters for the skills spec, not this one.

By the designer (my calls, open to veto):

- Conversation **title**: first 60 characters of the first message right away
  (sent in `start`). After the first reply's content is complete, still inside the
  same stream, the reply's model is asked for a title (≤ 6 words, thinking stripped);
  it is saved and sent as a `title` event before `done`. Best effort: on failure or
  after 60 s the fallback title stays. It runs inside the reply's run, so nothing
  needs polling.
- Deviation (built): title generated after `done`, detached, no `title` event — titles take ~70 s on this cluster.
- **Search** box in the sidebar over titles and message text.
- Sidebar grouped by **Today / Yesterday / Previous 7 days / Previous 30 days / month**.
- **Edit** a user message = replace its text, delete every later message, re-run the
  reply. **Regenerate** = delete the last assistant message, re-run. No branching or
  kept alternatives.
- Each assistant message records and shows the **model** that wrote it; the model can
  change mid-conversation. A new chat starts on the last model used (remembered in
  `localStorage`), else the first loaded one, else the first listed.
- The **picker lists LiteLLM's models only** (exo models absent from LiteLLM cannot be
  reached through it). Not-loaded models stay selectable, with a "not loaded" badge;
  DarkAngel never asks exo to load a model.
- **Thinking** is stored apart from the answer, shown in a collapsible "Thinking…"
  block (open while it streams, collapsed once the answer starts), and **never sent
  back** to the model in later turns.
- Background replies run **in the API process** (an asyncio task per reply, held in an
  in-memory registry). One uvicorn worker on one replica makes that enough.
  `# ponytail:` ceiling: more workers or replicas need a shared bus (Redis pub/sub or
  Postgres `LISTEN/NOTIFY`). A deploy or crash mid-reply loses the rest of that reply;
  what was saved so far stays, marked `stopped`.
- The sidebar shows a small spinner on conversations whose reply is still running.
- **One reply at a time per conversation**: a send while one is streaming → 409.
- **Markdown** via `marked`, sanitised by `DOMPurify`, code highlighted with
  `highlight.js` (common languages only), copy button on code blocks. Three new
  frontend dependencies, and the sanitiser is not optional.
- Delete = **soft delete** (`deleted_at`), as for files and folders.
- **System prompt** built server-side: a short assistant persona plus today's date and
  time (America/Toronto). Not user-editable in this spec. Memory and projects extend it.
- On narrow screens the sidebar becomes a drawer.

## Backend (`backend/app/`)

### Settings (`core/config.py`)

- `llm_url: str = ""`: LiteLLM base, e.g. `http://192.168.2.35:4000`. Empty = chat
  disabled: every `/api/chat/*` route returns 503 `Chat is not configured`.
- `llm_api_key: SecretStr = SecretStr("")`: the LiteLLM master key.
- `exo_url: str = ""`: e.g. `http://192.168.2.35:52415`. Empty = loaded status unknown.
- `llm_timeout_seconds: float = 300`: the longest wait **between** two stream chunks
  (prompt reading on a long chat can take minutes). Connect timeout 5 s.

### Data (`models/chat.py`, migration `0006_chat.py`)

`conversations`
- `id` uuid pk, `owner_sub` text not null (indexed), `title` text not null,
  `created_at`, `updated_at` (bumped on each new message: it drives sidebar order),
  `deleted_at` nullable.

`messages`
- `id` uuid pk, `conversation_id` uuid fk → conversations, not null, indexed.
- `role` text: `user` | `assistant`.
- `content` text not null (may be `""` for a stopped or failed reply).
- `reasoning` text nullable: the thinking, assistant only.
- `model` text nullable: the LiteLLM model name, assistant only.
- `status` text: `streaming` | `complete` | `stopped` | `error`.
- `error` text nullable: a message safe to show (no URLs, no key).
- `prompt_tokens`, `completion_tokens` int nullable (from the final `usage` chunk).
- `created_at`. Order within a conversation = `created_at, id`.

No reserved columns for later specs: each adds its own nullable column or table in its
own migration.

### Repository (`repositories/chat.py`)

`ChatRepository`, owner-scoped like `FolderRepository` (`owner_sub` first argument on
every method, so forgetting it is a TypeError): `list(owner_sub, q=None)`,
`get(owner_sub, id)`, `create(owner_sub, title)`, `rename`, `soft_delete`,
`messages(owner_sub, conversation_id)`, `add_message(...)`,
`truncate_after(owner_sub, message_id)` (hard-deletes later messages, for edit and
regenerate). Search `q` = case-insensitive substring on title or any message
content. A fake goes in `tests/conftest.py` next to the file and folder fakes.

### LLM client (`chat/llm.py`)

Thin `httpx.AsyncClient` code (httpx is already a dependency), separate from
`summary.ask_ollama`, which stays as it is.

- `models()`: LiteLLM `/v1/models` + `/model/info` (to get each model's exo id),
  joined with exo `/state` loaded ids →
  `[{id, loaded: true | false | null, context_length}]`. `null` = not an exo model,
  or exo unreachable. `context_length` = LiteLLM's `max_input_tokens` (present on
  `/v1/models`: 202 752 for GLM), else exo's `context_length` for that id, else
  32 768. Cached 10 s (the picker opens often; exo state changes rarely).
- `stream(model, messages)`: POST `/v1/chat/completions` with `stream: true`,
  `stream_options.include_usage`, no `max_tokens`. Thinking arrives as
  `reasoning_content` (verified on Qwen3.6 via exo); a `<think>…</think>` prefix inside
  `content` is also split out, for models that do it that way. Yields `("reasoning", text)`, `("content", text)`, `("usage", {...})`,
  and `("progress", processed, total)` when prefill comments get through.
- Upstream errors become one safe message: exo's "No instance found" and LiteLLM's
  cooldown → `"<model> is not loaded on the cluster"`; anything else →
  `"The model did not answer"`. Logged in full server-side, never echoed raw.

### Context sent to the model

`[system prompt] + every message of the conversation as {role, content}` (no
`reasoning`, and skipping `error` messages). If it would exceed the model's
`context_length`, the oldest messages are dropped first, estimating 4 characters per
token. `# ponytail:` comment on that estimate; a real tokenizer is the upgrade if it
misjudges.

### Routes (`api/routes/chat.py`, mounted at `/api/chat`, `Claims` dependency)

- `GET /models` → the picker list.
- `GET /conversations?q=` → `[{id, title, updated_at, streaming}]`, newest first.
- `POST /conversations` `{title?}` → the new conversation. Rarely needed: the first
  send creates one.
- `GET /conversations/{id}` → `{id, title, streaming: bool, messages: [...]}`.
- `PATCH /conversations/{id}` `{title}` → rename (1-200 chars).
- `DELETE /conversations/{id}` → 204, soft delete.
- `POST /conversations/{id}/messages` `{content, model, replace_from?: message_id}` →
  **`text/event-stream`**. `content` 1-100 000 chars. `replace_from` is the edit case
  (that message's text is replaced and everything after it deleted).
  `POST /conversations/{id}/regenerate` `{model}` → same stream, minus the last
  assistant message.
- A send to `id = "new"` creates the conversation first; the first event carries it.
- `GET /conversations/{id}/stream` → reattach to the running reply: the same event
  stream, starting with a `snapshot` event (the reasoning and content so far), then
  live deltas. No reply running → 204.
- `POST /conversations/{id}/stop` → 204. Cancels the running reply (no-op if none).

Every lookup filters on `claims["sub"]`. Another user's id → 404, never 403.

Stream events (named SSE events, JSON data):

| event | data |
|---|---|
| `start` | `{conversation: {id, title}, user_message: {...}, assistant_message_id}` |
| `snapshot` | `{reasoning, content}` so far (reattach only) |
| `progress` | `{processed, total}` (only if prefill comments arrive) |
| `reasoning` | `{text}` delta |
| `content` | `{text}` delta |
| `title` | `{title}` first reply only |
| `done` | `{message: {...}}` the saved assistant message |
| `error` | `{message}` safe text; the saved message has status `error` |

How a reply runs (`chat/runs.py`):

- The send route commits the user message and an assistant message with status
  `streaming`, then starts a `Run`: an `asyncio.Task` that reads the LLM stream,
  appends each event to the run's buffer and pushes it to every subscriber queue.
  The HTTP response is just one subscriber; the reattach route adds another.
- **A client disconnecting only unsubscribes.** It never cancels the task. Only
  `POST /stop` cancels it.
- The run owns its database work and uses its own sessions (`session_factory`, as
  `summary.py` does), never the request's. It writes the accumulated `content` /
  `reasoning` to the assistant row about every 2 s, and once more at the end with
  `complete`, `stopped` (cancelled) or `error`. Then it generates the title (first
  reply only) and leaves the registry.
- The registry is a dict of conversation id → `Run`. It is also the 409 guard: a send
  or regenerate while a run exists for that conversation → 409.
- **At startup** (`create_app` lifespan), every message still `streaming` is set to
  `stopped`: its run died with the previous process.
- Stream responses send `X-Accel-Buffering: no` so nginx does not buffer them.

## Frontend (`frontend/src/`)

- `api/client.ts`: add `apiStream(path, body, signal)`, an async generator over named
  SSE events, built on `fetch` + `ReadableStream` (not `EventSource`, which cannot
  send the bearer token). It stays the only place `fetch` is called.
- `api/chat.ts`: types and calls for the routes above.
- `stores/chat.ts`: Pinia setup store: conversation list, the open conversation,
  the streaming message, `loading` / `error`. It follows **one** stream, the open
  conversation's. Opening another conversation or leaving `/chat` aborts the `fetch`
  (the server keeps going). Opening a conversation with `streaming: true` calls the
  reattach route. Stop calls `POST /stop`. When a run ends, the sidebar list is
  refetched so spinners and titles update.
- `views/ChatView.vue` at `/chat` and `/chat/:id` (lazy-loaded); **Chat** link in
  `App.vue` between Household and About.
- `components/chat/`: `ChatSidebar.vue` (new chat, search, grouped list, ⋯ rename /
  delete), `ChatMessage.vue` (markdown, thinking block, model label, copy / edit /
  regenerate, error with Retry), `ChatComposer.vue` (auto-growing textarea, Enter
  sends, Shift+Enter newline, model picker, Send ↔ Stop), `ModelPicker.vue`.
- `markdown.ts`: `marked` → `DOMPurify.sanitize` → `v-html`. The **only** place model
  text becomes HTML.
- While waiting for the first token: "Reading your message… n/N" if `progress`
  arrives, else a "Waiting for the model…" pulse with elapsed seconds.

## Testing (see `docs/testing.md`)

- unit (backend): `chat/llm.py` against `httpx.MockTransport` (SSE parsing, reasoning
  vs content, usage, both "not loaded" errors, model/loaded join, exo down → `null`);
  `chat/runs.py` with a fake LLM (a disconnected subscriber does not stop the run;
  reattach gets snapshot + live deltas; `/stop` saves `stopped`; the startup sweep);
  routes against a fake repository and a fake LLM (owner scoping → 404, 503 when
  unconfigured, 409 on a concurrent send, edit/regenerate truncation, context
  trimming).
- integration: `ChatRepository` against Postgres (search, truncate, soft delete,
  owner scoping).
- regression: OpenAPI snapshot updated.
- unit (frontend): `apiStream` parsing split chunks; the store's send / stop / edit,
  and reattach when opening a conversation with `streaming: true`;
  `markdown.ts` strips `<script>`, `onerror=` and `javascript:` links; sidebar
  date grouping.

## Deployment

- Stack variables: `DARKANGEL_LLM_URL=http://192.168.2.35:4000`,
  `DARKANGEL_LLM_API_KEY` (Portainer variable, never in git),
  `DARKANGEL_EXO_URL=http://192.168.2.35:52415`.
- Owner, before go-live: add the loaded exo model(s) to LiteLLM's `model_list`;
  rotate the master key (it appeared in a design chat).
- A redeploy interrupts any reply in progress (saved as `stopped`).
- The API container must reach `192.168.2.35:4000` and `:52415`, as it already
  reaches Ollama on the same host.

## Later specs (so this one does not block them)

- **Memory**: extends the system prompt builder; a new `memories` table.
- **Projects**: `conversations.project_id` added then; project instructions join the
  system prompt.
- **Files**: attachments reuse `summary.py`'s text extraction; a `message_files` link.
- **Skills**: the stream loop gains a tool-call round trip; `messages` gains
  `tool_calls` / `tool` role. Tool calling is verified to work on the loaded model.
- **Artifacts**: parsed from assistant content; rendered in an
  `<iframe sandbox="allow-scripts">` **without** `allow-same-origin`.

## Left to the builder

Component internals and styling; the exact system prompt wording; the title prompt
(and whether to pass a no-thinking flag, if exo honours one); the SSE parser
implementation; how the sidebar drawer opens on mobile.
