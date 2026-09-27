# Files Phase 2: folders, metadata and search

Date: 2026-09-26 · Status: approved design, awaiting spec review

## Goal

Deliver Phase 2 of `docs/files-feature.md` ("Organise and find"): a user can put their
files in nested folders, rename / describe / tag / move them, and find them again by
search, tag filter and sort, all from the SPA.

## Current state (`origin/main` at 33af1f3)

- Phase 1 is merged: Postgres is the index, MinIO keys are `<sub>/<uuid>`, files are
  id-addressed, delete is soft (`deleted_at`), uploads go `pending` → `ready`.
- The schema already has everything Phase 2 needs: `folders(id, owner_sub, parent_id,
  name, deleted_at)` with the `uq_folders_sibling_name` unique index (`NULLS NOT DISTINCT`),
  and `files.folder_id`, `files.description`, `files.tags text[]` with a GIN index.
  **No migration is required.**
- `FileRepository.list()` takes only `limit`/`offset`; `upload_file` hard-codes
  `folder_id=None`. There is no folder route, no `PATCH /api/files/{id}`.
- The SPA shows one flat table with upload, download and delete.

## Decisions

| Question | Decision |
|---|---|
| Scope | All of Phase 2: folders, rename, description, tags, search, filter, sort, pagination. |
| Navigation | Breadcrumb drill-down. One table shows the current folder, subfolders first. No tree sidebar. |
| Non-empty folder delete | `409` with child counts; SPA confirms and retries with `?recursive=true` (BR-7). |
| Search scope | Across all the user's folders. Results show a Location column. |
| Editing | One native `<dialog>` per row, one `PATCH` on save. Also used for folders. |
| Search engine | Plain `ILIKE` + tag array containment. No `pg_trgm`, no `tsvector`. |
| Name clash on rename/move | `409`. BR-2's merge-into-new-version applies to uploads only. |
| Pagination | Existing `limit`/`offset`; SPA shows "Load more". |

## Backend

### API

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/api/folders` | Flat list of the caller's live folders: `[{id, name, parent_id}]`. |
| `POST` | `/api/folders` | Body `{name, parent_id?}` → `201` with the folder. |
| `PATCH` | `/api/folders/{id}` | Body `{name?, parent_id?}`: rename and/or move. `parent_id: null` moves to root. |
| `DELETE` | `/api/folders/{id}` | `204` if empty. Non-empty: `409 {detail, folders, files}` unless `?recursive=true`. |
| `GET` | `/api/files` | Adds query `folder_id` (absent = root), `q`, `tag`, `sort`, `order`. |
| `POST` | `/api/files` | Adds optional multipart field `folder_id`. |
| `PATCH` | `/api/files/{id}` | Body `{name?, description?, tags?, folder_id?}` → `200` with the file. |

`FileInfo` gains `folder_id`, `description` and `tags`. `FolderInfo` is `{id, name,
parent_id}`. Both pydantic models sit next to their route (CLAUDE.md convention).

In `PATCH` bodies, an omitted field is unchanged and an explicit `null` for `folder_id` /
`parent_id` means root (`model_fields_set` distinguishes the two).

### Listing and search

- Without `q` or `tag`: live, ready files whose `folder_id` equals the given folder
  (`IS NULL` for root).
- With `q` and/or `tag`: `folder_id` is ignored and every live, ready file of the owner
  is a candidate.
  - `q` (1–200 chars, trimmed): `name ILIKE %q% OR description ILIKE %q% OR EXISTS
    (SELECT 1 FROM unnest(tags) t WHERE t ILIKE %q%)`. `%` and `_` in `q` are escaped.
  - `tag`: `tags @> ARRAY[tag]` (served by `ix_files_tags`).
  - Both given: AND.
- `sort` ∈ `name | size | updated_at` (default `updated_at`), `order` ∈ `asc | desc`
  (default `desc`). `name` sorts on `lower(name)`. `id` is the tiebreaker so paging is
  stable.
- `limit` (1–200, default 100) and `offset` stay as in Phase 1.

`# ponytail: ILIKE scans the owner's rows; add pg_trgm GIN indexes on name/description
if per-user file counts reach the tens of thousands.` goes on the search query.

### Folder rules

- **Names (BR-3/BR-4):** the same `_validated_name()` as files. Unique among the live
  siblings of the same owner, case-insensitively. This is enforced by
  `uq_folders_sibling_name`: the repository catches `IntegrityError` and the route
  returns `409 "A folder named X already exists here"`. No check-then-insert.
- **Parent:** `parent_id` must be a live folder of the caller, else `404`.
- **No cycles (BR-5):** on move, a recursive CTE walks the ancestors of the new parent.
  If it reaches the moved folder (or the new parent *is* the folder), `409`.
- **Delete (BR-7):** a folder is non-empty if it has any live child folder or live file.
  Without `recursive`: `409` with the counts of live descendant folders and files in the
  whole subtree. With `recursive=true`: one transaction sets one shared `deleted_at`
  timestamp on the folder, every live descendant folder, and every live file in them.
  Items already trashed keep their own timestamp, so Phase 3 can restore exactly the
  batch that was deleted.

### File rules

- **Rename / move:** name through `_validated_name()`; target folder must be a live
  folder of the caller, else `404`. A clash with a live file in the target folder hits
  `uq_files_folder_name` and returns `409 "A file named X already exists here"`.
- **Tags:** each trimmed and lowercased, empties and duplicates dropped, 1–50 chars
  each, at most 20 per file, else `422`. Stored in submission order.
- **Description:** at most 2000 chars; empty string stored as `NULL`.
- **Upload into a folder:** `folder_id` must be a live folder of the caller, else `404`.
  BR-2 (same name → new version) now applies per folder.
- No MinIO call is made by any `PATCH` or folder operation.

### Ownership and audit

- Every query filters on `owner_sub = claims['sub']`. A missing, foreign or trashed id
  returns `404` (§5 of `files-feature.md`).
- Each mutation writes one `audit_log` row: `folder_create`, `folder_rename`,
  `folder_move`, `folder_delete` (detail holds the counts for a recursive delete),
  `rename`, `move`, `retag` (also covers description). A single `PATCH` changing several
  fields writes one row per action, each with before/after in `detail`.

### Code layout

- `app/api/routes/folders.py` (new), included in `api/router.py`.
- `app/repositories/folders.py` (new): `list`, `get`, `create`, `update`, `is_cycle`,
  `subtree_counts`, `soft_delete_subtree`.
- `app/repositories/files.py`: `list()` gains the filters; new `update()`.
- `app/api/routes/files.py`: new `PATCH`, extended list and upload.

## Frontend

### State and routing

`/files` stays one route. `?folder=<id>` (absent = root), `?q=` and `?tag=` live in the
URL, so refresh, back and links work. The view watches `route.query` and reloads.

### Files

- `api/folders.ts` (new): `Folder` type, `listFolders`, `createFolder`, `updateFolder`,
  `deleteFolder(id, recursive)`.
- `api/files.ts`: `HomeFile` gains `folder_id`, `description`, `tags`;
  `listFiles(params)`; `uploadFile(file, folderId)`; `updateFile(id, patch)`.
- `stores/files.ts`: adds `folders` (flat list, reloaded after any folder mutation),
  `pathOf(id)` (walks `parent_id` to the root, used by the breadcrumb, the Location
  column and the `<select>` labels) and `loadMore()`. Keeps `run()` for loading/error.
- `views/FilesView.vue`:
  - Breadcrumb `Home › A › B`, each segment a `<router-link>`.
  - Search input, debounced 300 ms, writes `?q`. Tag chips in rows set `?tag`; an active
    tag shows as a removable chip.
  - "New folder" button → `EditDialog` in create mode.
  - Browse mode: subfolders first (click → navigate), then files. Search mode: files
    only, plus a Location column linking to each file's folder.
  - Sortable Name / Size / Modified headers (`sort`/`order` in local state, not the URL).
  - "Load more" when the last page came back full.
- `components/EditDialog.vue` (new): native `<dialog>`. File mode: name, description,
  tags (comma-separated), folder `<select>`. Folder mode: name, parent `<select>` that
  excludes the folder and its descendants. Save → one `PATCH`/`POST`; a `409` or `422`
  message is shown inside the dialog and it stays open.

### Folder delete flow

Delete → `confirm("Delete <name>?")` (same as a file row), then `deleteFolder(id, false)`.
On `204` done. On `409`, a second
`confirm("Delete <name> and its N folders / M files?")`, then
`deleteFolder(id, true)`. Uploads go into the current folder.

No optimistic updates: every mutation reloads, as today.

## Testing

Per `docs/testing.md`: the directory decides the marker.

- **Backend unit** (fake repository): validation (names, tags, description, `q`),
  error mapping (404/409/422), `q`/`tag` overriding `folder_id`, `PATCH` null-vs-omitted.
- **Backend integration** (real Postgres, real MinIO): cycle CTE, subtree counts,
  recursive soft-delete with the shared timestamp and untouched pre-trashed items,
  sibling-name `409` from the index, `ILIKE` escaping, tag containment, sort + paging
  stability. The fake repository must match these behaviours (see the fake-drift lesson
  from Phase 1).
- **Backend regression:** extend `test_r1_cross_owner_isolation.py` so every new folder
  route, `PATCH /api/files/{id}` and upload with a foreign `folder_id` returns `404` and
  changes nothing. Regenerate `openapi.snapshot.json`.
- **Frontend unit:** `pathOf`, breadcrumb rendering, search vs browse mode, the delete →
  409 → confirm → recursive flow, `EditDialog` showing a `409` and staying open.
- **Acceptance:** the five Phase 2 scenarios in `files-feature.md` §12, one test each.

## Out of scope

- Trash view, undelete, version history (Phase 3).
- Drag & drop, upload progress, preview, quota indicator (Phase 4).
- Folder tree sidebar; multi-select move or delete.
- `pg_trgm` / full-text search (upgrade path noted above).
