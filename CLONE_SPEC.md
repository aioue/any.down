# Any.do Clone — Product & API Spec

Captured from live browser session analysis (network performance log, IndexedDB, incremental sync API). Intended as a blueprint for a self-hosted replacement, not a technology match.

---

## Goals

- Own your task data and attachments
- Preserve workflows that work well in Any.do: lists, tags, smart views, subtasks, due dates, completion vs archive
- Support reliable backup/export (lessons from `anydown`)

---

## UI Layout

Three-pane layout (desktop):

| Pane | Contents |
|------|----------|
| **Left sidebar** | Account, smart views, lists, tags, shared spaces |
| **Center** | Filtered task list, grouped by date section |
| **Right** | Task detail: title, remind/list/tags pills, notes, subtasks, attachments |

### Smart views (sidebar)

| View | Behavior |
|------|----------|
| **My day** | Tasks pinned/planned for today (`myDayEntry` model) |
| **Next 7 days** | Due within 7 days |
| **All my tasks** | Cross-list; grouped Today / Tomorrow / Upcoming / Someday |
| **My Calendar** | Calendar integration |

### List view

- One list at a time
- Tasks filtered by `categoryId`
- Same date grouping as All my tasks
- URL: `/tasks/lists/{categoryId}`

### Tag view

- Cross-list filter: tasks where `labels` contains tag ID
- Same date grouping
- Tag name as page heading; shows source list in task row
- URL: `/tags/tag/{labelId}` (URL-encoded `==` in IDs)

### Task detail

- Deep link: `/tasks/all/tasks/{globalTaskId}` (also works under list/tag routes)
- Breadcrumb: `My lists > {list name}`
- Actions: Mark as complete, focus, archive
- Sections: Notes, Subtasks (with `n/m` progress), Attachments (drag/drop)

### Toolbar (center column)

- **View** — grouping options (e.g. by time)
- **Filter** — by list, tag, status (Active / Marked as complete)
- **More** — multi-select, print
- **Clear completed** — bulk remove completed from view

---

## Routing (SPA, client-side)

Navigation does **not** hit the API. Routes observed:

```
/tasks/all
/tasks/all/tasks/{globalTaskId}
/tasks/lists/{categoryId}
/tags/tag/{labelId}
```

Filter state in query string, e.g. `?filters={"done":["y"]}` for completed-only.

---

## Task Status Lifecycle

| Status | Meaning | UI visibility |
|--------|---------|---------------|
| `UNCHECKED` | Active | Default views |
| `CHECKED` | Completed | Hidden by default; filter "Marked as complete" |
| `DONE` | Archived | Hidden; requires `includeNonVisible=true` on sync |
| `DELETED` | Soft-deleted | Excluded from normal views |

**Complete vs archive are different:**

- **Complete** → `CHECKED`. Task stays in DB; can be filtered/viewed.
- **Archive** → `DONE`. Removed from local IndexedDB; still on server with `includeNonVisible=true`. Subtasks may disappear from server while parent remains.

`anydown` currently treats only `CHECKED` as completed and uses `includeNonVisible=false`, so **archived tasks and attachments are under-exported**.

### Completion interaction (observed)

1. User clicks checkbox on "Create test task"
2. Confirmation dialog ("Yes") — optional for some flows
3. **Optimistic UI**: animation plays immediately
4. IndexedDB updated: `status: CHECKED`, `statusUpdateTime` set
5. Background sync pushes change; confirmed via `bg_sync` → `bg_sync_result`
6. No `PUT /me/tasks` visible in browser Performance API (mutations go through sync engine)

**Completion mutation payload** (only changed fields matter; per-field `*UpdateTime` timestamps):

```json
{
  "globalTaskId": "4OlWxUDln1He_PX1fgu-jqRU",
  "status": "CHECKED",
  "statusUpdateTime": 1784298291567,
  "lastUpdateDate": 1784298291567
}
```

---

## Data Model

### Task

```typescript
interface Task {
  id: string
  globalTaskId: string           // client-generated, 24 chars; often equals id
  title: string
  note: string
  status: 'UNCHECKED' | 'CHECKED' | 'DONE' | 'DELETED'
  priority: 'Normal' | 'High' | 'Low'
  categoryId: string             // list FK
  parentGlobalTaskId: string | null  // subtask FK
  dueDate: number | null         // ms epoch; 0 or null = none
  labels: string[] | null        // tag IDs
  position: string               // hex string for sort order
  repeatingMethod: string        // e.g. TASK_REPEAT_OFF
  assignedTo: string | null
  shared: boolean
  participants: string[]
  alert: Reminder | null
  creationDate: number
  lastUpdateDate: number
  // Per-field sync timestamps (all ms epoch):
  titleUpdateTime, noteUpdateTime, statusUpdateTime,
  categoryIdUpdateTime, dueDateUpdateTime, labelsUpdateTime,
  priorityUpdateTime, positionUpdateTime, alertUpdateTime, ...
  subTasks: []                   // always empty in API; subtasks are separate rows
}
```

### Subtasks

- Separate `Task` rows with `parentGlobalTaskId` set
- Subtask positions use a different range (e.g. `8004`, `8008`) from parent (`6273`)
- Parent shows progress `SUBTASKS 2/2` in UI

### List (category)

```typescript
interface List {
  id: string
  name: string
  position: string
  isDefault: boolean
  isDeleted: boolean
  isGroceryList: boolean
  lastUpdateDate: number
}
```

### Tag (label)

```typescript
interface Tag {
  id: string
  name: string
  color: string        // hex, e.g. #ff6168
  isDeleted: boolean
  isPredefined: boolean
}
```

Tasks reference tags by ID in `labels[]`. Tag view = filter `labels.includes(tagId)`.

### Attachment

```typescript
interface Attachment {
  id: string
  globalTaskId: string
  displayName: string
  mimeType: string
  fileSize: number
  url: string              // public S3 URL
  deleted: boolean
  uploaderId: string
  creationDate: number
  lastUpdateDate: number
}
```

Separate model/store — not embedded in task JSON.

### My Day entry

```typescript
interface MyDayEntry {
  id: string
  referencedObjectType: string
  referencedObjectId: string   // task globalTaskId
  date: number
  status: string
  position: string
}
```

### Reminder (alert)

`type` values: `"NONE"` (placeholder, no reminder) or `"OFFSET"` (real reminder).

```json
{
  "type": "OFFSET",
  "offset": 0,              // minutes before dueDate (-1 = no offset?)
  "customTime": 0,
  "repeatInterval": 1,      // how often to repeat (1 = every time)
  "repeatStartsOn": 1583231400000,   // when recurrence begins (ms)
  "repeatEndsOn": null,
  "repeatEndsAfterOccurrences": -1,  // -1 = forever; 1 = once
  "repeatDays": "0000010",  // bitmask Mon-Sun (1=active day)
  "repeatMonthType": "ON_DATE",
  "repeatEndType": "REPEAT_END_NEVER",
  "repeatNextOccurrence": 1584095400000  // pre-computed next fire time
}
```

Most tasks have `alert: null` or `alert.type: "NONE"`. Real reminders use `"OFFSET"` with `offset: 0` (at due time).

### Repeating tasks

| `repeatingMethod` | Meaning |
|----|-----|
| `TASK_REPEAT_OFF` | No recurrence (default) |
| `TASK_REPEAT_DAY` | Daily |
| `TASK_REPEAT_WEEK` | Weekly (uses `repeatDays` bitmask) |
| `TASK_REPEAT_YEAR` | Yearly |

Repeat config lives in the `alert` object, not as separate fields. `repeatDays` is a 7-char bitmask `"0000010"` where position = Mon through Sun.

### Position / ordering

Hex strings for sort order within a view:

| Context | Range | Example |
|---------|-------|---------|
| Parent tasks | `6277` – `81e8` | `"6273"`, `"627b"`, `"62db"` |
| Subtasks | `8000` – `8040` | `"8004"`, `"8008"` |

Subtask positions are in a **separate namespace** from parents. Positions increment by ~4 hex units when adding tasks sequentially.

### My Day entries

My Day is a **join table** — separate model linking tasks to specific days:

```json
{
  "id": "uuid",
  "referencedObjectType": 0,        // 0 = task
  "referencedObjectId": "taskId",
  "status": 0,
  "visibilityStatus": 2,            // 2 = visible, 3 = dismissed/completed?
  "position": "7fec",
  "date": 1780823903000,            // when it was added to My Day
  "lastUpdateDate": 1780827719823
}
```

**Reset time:** `myDayConfig.myDayResetHourOfDay = "0500"` — My Day clears at 5am local time. Configurable per user.

---

## API Architecture

**Base URL:** `https://sm-prod4.any.do`

**Auth:** `X-Anydo-Auth` header = base64 token from `localStorage.auth`  
(decodes to `email:timestamp:hash`)

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/me` | User profile + `webConfig` |
| GET | `/api/v14/me/bg_sync?updatedSince={ms}&includeNonVisible={bool}` | Start async sync |
| GET | `/me/bg_sync_result/{task_id}` | Poll sync result (200 when ready) |
| PUT | `/me/tasks` | Create/update tasks (batch array) — used by CLI, not visible in web UI |
| GET | `/me/tasks/{id}` | Single task + embedded `subTasks` (~4 KB); used by SDK clone path |
| GET | `/me/tasks?parentGlobalTaskId={id}` | **Broken filter** — returns all tasks (~3 MB); do not use |
| GET | `/me/attachments?globalTaskId={id}` | Attachments for one task |
| PUT | `/me/attachments` | Register attachment row (after S3 upload or clone re-link) |
| DELETE | `/me/tasks/{id}` | Delete task |
| GET | `/me/request_s3_presigned_post?S3ObjectType={mime}&S3ObjectName={name}&UploadType=attachment` | S3 upload credentials |
| GET | `/me/myday/suggestions?locale={lang}&date={DDMMYY}` | My Day task suggestions |
| GET | `/me/myday/external_suggestions?locale={lang}&date={DDMMYY}` | Calendar-based suggestions |
| GET | `/me/completed_tasks?page={n}` | Paginated completed task history |
| GET | `/me/updates/user_updates?language={lang}&pageSize={n}&importantOnly={bool}&before={ts}` | Activity/notification feed |
| GET | `/me/calendar_providers/fetch` | Calendar integrations |
| GET | `thumb-server.any.do/get_thumbnail?url={s3}&w={}&h={}` | Thumbnail for UI |

### Sync flow

```
1. GET /api/v14/me/bg_sync?updatedSince={lastSyncMs}&includeNonVisible=true
   → { task_id, polling_interval, total_timeout }

2. Poll GET /me/bg_sync_result/{task_id} until 200

3. Response:
   {
     models: {
       task: { items: [...] },
       category: { items: [...] },
       label: { items: [...] },
       attachment: { items: [...] },
       myDayEntry: { items: [...] },
       ...
     },
     userInfo: {...},
     lastUpdateDate: number
   }
```

- **Incremental:** `updatedSince` = last sync timestamp (ms)
- **Full:** `updatedSince=0` (rate-limited ~60s on Any.do)
- **Local cache:** IndexedDB `anydo-sync-db` with per-model object stores

### Web client mutation model

The web app does **not** call REST for each edit. Instead:

1. Optimistic write to IndexedDB
2. Sync engine batches outbound changes
3. Inbound confirmation via `bg_sync` polling

For a clone, a simpler **direct REST** or **WebSocket sync** is fine and easier to reason about.

### anydown SDK: three read paths (why each exists)

| Path | Size | When to use |
|------|------|-------------|
| **Agent export** / `GET /agent` | ~70–110 KB | Default for agents — pending tasks with IDs, notes, subtask summaries. Not sync-shaped; no attachments. |
| **Per-task REST bundle** | ~few KB | `clone_task` / `recreate_task` — `GET /me/tasks/{id}` + embedded `subTasks` + `GET /me/attachments?globalTaskId=`. Avoids full sync. |
| **bg_sync full** (`updatedSince=0`) | ~900 KB | Bulk queries, archived/deleted models, attachment model for whole account. Rate-limited 60s. |

**Incremental bg_sync** (`updatedSince=lastSync`) is for backup/watch mode: when nothing changed it returns empty/sparse models (zero task rows is normal). Do not rely on it for single-task lookups.

### anydown SDK: two mutation paths (reliability split)

| Operation | API path | Cookie session reliability |
|-----------|----------|----------------------------|
| **Create** (new `globalTaskId`) | `PUT /me/tasks` | Reliable — title, note, due, alert, tags, subtasks |
| **Update** (existing row) | `POST /api/v14/me/sync` (web) or `PUT /me/tasks` (SDK fallback) | Unreliable echo — SDK confirms via `GET /me/tasks/{id}` per affected task only (~4 KB), not full sync |
| **Rename workaround** | `recreate_task` = create clone + delete source | Reliable for content; **task ID always changes** |
| **Delete** | `DELETE /me/tasks/{id}` | Reliable |
| **Reorder / position** | Sync engine only | Not available via SDK today |
| **creationDate after create** | — | Immutable (server ignores PUT/sync push) |

Completed subtasks on REST use status `DONE`; sync export and PUT create use `CHECKED`. SDK normalizes on clone.

**Verification after update:** sync/PUT responses often echo stale DTOs. anydown re-fetches only tasks whose echo mismatched (`GET /me/tasks/{id}`), and uses the same per-task GET to build sync push payloads when no `tasks_data` is supplied — avoiding incremental/full sync for single-task edits.

---

## Attachment Upload Flow

Observed sequence for PNG upload:

```
1. GET /me/request_s3_presigned_post
     ?S3ObjectType=image/png
     &S3ObjectName=Screenshot 2026-04-26 at 14.04.08.png
     &UploadType=attachment

2. Response:
   {
     "url": "https://s3.us-east-1.amazonaws.com/anydo-user-uploads/",
     "fields": {
       "acl": "public-read",
       "Content-Type": "image/png",
       "key": "{userIdPrefix}_{uuid}_{filename}",
       "AWSAccessKeyId": "...",
       "policy": "<base64>",
       "signature": "..."
     }
   }

3. POST multipart/form-data to S3 url with fields + file

4. Final URL:
   https://s3.us-east-1.amazonaws.com/anydo-user-uploads/
     {userIdPrefix}_{uuid}_{sanitized_filename}

5. GET thumb-server.any.do/get_thumbnail?url=...&w=522&h=325

6. Attachment record appears in next bg_sync (linked by globalTaskId)
```

**S3 key pattern:** `{userIdSansLeadingUnderscore}_{randomUuid}_{filename_with_underscores}`

### Clone implementation sketch

```
POST /api/attachments/presign  → presigned POST fields
POST S3/MinIO directly
POST /api/attachments         → { taskId, url, displayName, mimeType, fileSize }
```

---

## List vs Tag filtering

| View type | Filter | Example |
|-----------|--------|---------|
| List | `task.categoryId === listId` | `/tasks/lists/CryyoYb6zIdTKnrriMAyZS4v` → "Puchase in City" |
| Tag | `task.labels.includes(tagId)` | `/tags/tag/042_SH9yT-e37vbF93Rn6A==` → "#Lin" (2 tasks) |

A task can be in one list and multiple tags. Tag view is cross-list.

---

## Date Grouping (All my tasks, list view, tag view)

Tasks grouped into sections by due date:

| Section | Rule |
|---------|------|
| **Today** | due today or overdue |
| **Tomorrow** | due tomorrow |
| **Upcoming** | due later with a date |
| **Someday** | no due date |

Tasks without due dates appear under Someday. List name shown under each task title in cross-list views.

---

## Suggested Clone Schema (Postgres)

```sql
users (id, email, timezone, settings jsonb)

lists (id, user_id, name, position, is_default, deleted_at)

tags (id, user_id, name, color, deleted_at)

tasks (
  id, user_id, list_id, parent_id,
  title, note, status, priority,
  due_at, position,
  repeating_method, reminder jsonb,
  created_at, updated_at, completed_at, archived_at
)

task_tags (task_id, tag_id)

attachments (
  id, task_id, display_name, mime_type,
  file_size, storage_key, url, created_at
)

my_day_entries (id, user_id, task_id, date, position)
```

---

## MVP Feature Order

1. **Auth + lists + tasks** (CRUD, status, due dates)
2. **Smart views** — All tasks with date grouping; single list view
3. **Subtasks** — parent_id, progress counter
4. **Complete flow** — optimistic UI + checkbox animation
5. **Tags** — tag view, colored sidebar, filter
6. **Sync** — incremental pull/push with per-field timestamps
7. **Attachments** — presigned upload + metadata
8. **Archive** — separate from complete (`DONE` vs `CHECKED`)
9. **My Day** — pin tasks to today
10. **Reminders** — alert object + notifications

---

## UX Details Worth Copying

- **Optimistic updates** — checkbox/complete animates before server confirms
- **Badge counts** on sidebar items (e.g. "Personal 99+", "#Buy 62")
- **Quick add** at bottom of task list
- **Detail panel** stays open while browsing list
- **Subtask inline add** with checkbox in detail pane
- **Attachment drop zone** with dashed border
- **Dark mode** default in web app

---

## Lessons for `anydown` / Migration

| Gap | Fix |
|-----|-----|
| Misses `DONE` (archived) tasks | Sync with `includeNonVisible=true`; treat `DONE` as archived |
| No attachment download | Export `attachment` model; fetch S3 URLs |
| Subtasks | Already handled via `parentGlobalTaskId` |
| Full sync rate limit | Respect 60s cooldown |

---

## Observed Test Data (session 2026-07-17)

### Round 1

| Action | Result |
|--------|--------|
| Created "Create test task" + note | `4OlWxUDln1He_PX1fgu-jqRU`, Personal list |
| Added subtasks | `ojZZtMr7G-LHpG9BcC2gSUmV`, `vdoiWJd2oG5FEpOPtGxWvoLz` |
| Uploaded PNG attachment | `8QaB0bZLQ07bt9k9bkp-QWge`, 52KB |
| Marked complete | `status: CHECKED` @ 1784298291567 |
| Clicked list "Puchase in City" | `/tasks/lists/CryyoYb6zIdTKnrriMAyZS4v` — no API call |
| Clicked list "App/Business Idea" | `/tasks/lists/4vconRhqSRyGb0a7Lv_hQw==` — no API call |
| Clicked tag "#Priority" | `/tags/tag/I1a1xvKwXPz_JpDD8g_xtA==` — no API call |
| Clicked tag "#Lin" | `/tags/tag/042_SH9yT-e37vbF93Rn6A==` — 2 tasks, no API call |

### Round 2

| Action | Result |
|--------|--------|
| Opened My Day view | `/myday` — triggered 3 new endpoints (see below) |
| Opened Notifications panel | `GET /me/updates/user_updates?language=en&pageSize=20&importantOnly=false&before={ts}` |
| Viewed completed tasks history | `GET /me/completed_tasks?page=0` — paginated, returned ~100KB |
| Created "test task" (no due, cancelled reminder) | `mfeQTV32u-UkK-nAUFtpUghI` — `alert.type: "NONE"`, `alert.offset: -1` |
| Completed "test task" | Went to `CHECKED`, removed from IndexedDB (gone from local) |
| Created task with tag (#Lin) | `CBuHs4gfjQzJYLicAp_e9HMW` — `labels: ["042_SH9yT-e37vbF93Rn6A=="]` |
| Created task with due+reminder "Sat 12:00" | `6BQ9BvCTs8CMGsHzfjmiMGE7` — `dueDate: 1784372400000`, `alert.type: "OFFSET"`, `alert.offset: 0` |
| Browsed several tasks in detail panel | URL updated per task, no API calls |

---

## New Endpoints Discovered (Round 2)

### My Day Suggestions

```
GET /me/myday/suggestions?locale=en&date=170726
```

Server-side suggestions for what to add to My Day:

```json
[
  {
    "referencedObjectType": 0,
    "referencedObjectId": "taskId",
    "cause": "Due today",
    "shortcuts": ["due_today"],
    "sortKeyB64": "..."
  }
]
```

Causes observed: `"Due today"`. The app shows these as suggestions you can accept/dismiss.

### External Suggestions (calendar events)

```
GET /me/myday/external_suggestions?locale=en&date=170726
```

Calendar-based suggestions for My Day (same date format: `DDMMYY`).

### Completed Tasks (paginated history)

```
GET /me/completed_tasks?page=0
```

Returns `{ data: [...] }` with minimal task records:

```json
{
  "id": "...",
  "globalTaskId": "...",
  "title": "...",
  "statusUpdateTime": 1784297892865,
  "categoryId": "..."
}
```

~100KB for page 0 = large history. Sorted by `statusUpdateTime` descending. This powers the "Marked as complete" filter.

### Notifications / Activity Feed

```
GET /me/updates/user_updates?language=en&pageSize=20&importantOnly=false&before={timestamp}
```

Returns `{ is_last_page: true, data: [] }`. Paginated cursor with `before` timestamp.

---

## Additional Observations

### Reminder semantics

| `alert.type` | `alert.offset` | Meaning |
|------|------|---------|
| `"OFFSET"` | `0` | Remind at due time |
| `"NONE"` | `-1` | User cancelled / dismissed reminder |
| `null` | — | No reminder ever set |

### Due date + time in UI

Task with `dueDate: 1784372400000` shows as `"Tomorrow, 12:00"` in the detail panel. The reminder checkbox appears **checked** when `alert.type === "OFFSET"`.

### Smart time parsing

The task creation flow appears to detect time in typed text (e.g. "tomorrow 3pm") — configurable via `webConfig.smartTypeTimeDetection: true`.

### Add Task quick-create panel

When creating from quick-add, a panel appears with:
- **List** selector (shows current list)
- **Labels** selector (tags)
- **"Add N tasks?"** button (for multi-line paste)
- **Close** button

This suggests multi-task paste creates one task per line.

### Badge counts update live

Sidebar badge updated from `#Lin 2` to `#Lin 3` after adding the tagged task. Counts are computed client-side from IndexedDB, not fetched from API.

### Completed tasks removed from IndexedDB

"test task" (completed in round 2) is **gone** from local IndexedDB but exists in `GET /me/completed_tasks`. This confirms the pattern: once `CHECKED`, the web client evicts from the local store after sync.

---

## Out of Scope (for v1)

- Boards / cards / kanban (`board`, `card`, `section` models exist but unused in this account)
- Shared spaces / collaboration
- Calendar provider sync
- Alexa / Google Assistant list sync
- Premium billing / Stripe
- A/B experiments (`abservice3.any.do`)

---

## User Preferences (webConfig)

Stored server-side on the user object:

| Setting | Value | Purpose |
|---------|-------|---------|
| `theme` | `0` (light) / `1` (dark) | UI theme |
| `dateFormat` | `1` | Date display format |
| `timeFormat` | `1` | 12h vs 24h |
| `language` | `"en"` | UI language |
| `firstDayOfWeek` | `1` (Monday) | Calendar start |
| `defaultView` | `"lastviewed"` | What loads on app open |
| `defaultViewLastViewed` | `"myday"` | Last used view |
| `showWeekend` | `false` | Calendar toggle |
| `smartTypeTimeDetection` | `true` | Parse "tomorrow 3pm" from typed titles |
| `voiceEnabled` | `true` | Voice input |
| `myDayResetHourOfDay` | `"0500"` | When My Day resets |
| `completedCounter` | `2849` | Lifetime tasks completed (gamification) |

Background image is configurable via Unsplash (`selectedImage` stores an Unsplash ID + URL).

---

## Priority

Only `"Normal"` is used across all 516 tasks in this account. The field supports `"Normal" | "High" | "Low"` but in practice appears unused. Worth supporting but don't over-invest in UI for it.

---

## anydown sync freshness (agent pitfall)

| Path | Appears in incremental `bg_sync`? | Notes |
|------|-----------------------------------|-------|
| Web/native `POST /api/v14/me/sync` | Yes | Primary mutation path |
| `PUT /me/tasks` create (new row) | **No** | Used by `anydown` clone/recreate |
| `DELETE /me/tasks/{id}` | **No** | Soft-delete via REST |
| `PUT /me/tasks` update (existing) | Unreliable echo | Often stale on cookie sessions |

`anydown` tracks `last_mutation_timestamp` vs `last_sync_timestamp` in session and agent export (`sync_stale`). When stale, incremental sync and cached agent JSON can omit recent creates — agents must full-sync or use per-task `GET /me/tasks/{id}` on the mutating session before retrying writes.

Homelab `GET /agent` serves disk cache unless `?live=1`; mutations from a dev-machine SDK session are invisible until the container syncs.

---

## Analytics / Telemetry (skip for clone)

- **AWS Kinesis** — event stream, fires on every interaction (~10+ calls during session)
- **Facebook Pixel** — `PageView` on route changes, `SubscribedButtonClick` on interactions
- **Google Analytics** — standard GA4 page_view events
- **Cognito** — identity federation (for Kinesis credentials)

None of this is needed for the clone. Mentioned only so you recognise these in network logs.
