# Fixes Session Notes

Date: 2026-06-03

This file summarizes the fixes applied during the Windows local run/debug session.

## Runtime Setup

Backend should be run on Windows without `--reload` when using Playwright/CloakBrowser:

```powershell
cd "D:\AI-PROJECT HUNTER\Browser_automation-main\backend"
.\.venv\Scripts\uvicorn.exe app.main:app --port 8088 --loop asyncio
```

Frontend:

```powershell
cd "D:\AI-PROJECT HUNTER\Browser_automation-main\frontend"
npm.cmd run dev
```

URLs:

- Frontend: http://localhost:3001
- Backend docs: http://localhost:8088/docs
- Health: http://localhost:8088/health

## Fixes Applied

### 1. Correct `.env` usage

The backend reads `backend/.env`, not `.evn`.

The `.env` file was created from `.env.example` and later populated with:

- `GEMINI_API_KEY`
- `CAPSOLVER_API_KEY`
- `OPENAI_API_KEY` can stay empty if Gemini is used.

### 2. Multiple Gemini API keys

Files changed:

- `backend/app/core/config.py`
- `backend/app/services/llm_keyring.py`
- `backend/app/services/signup/agent_runner.py`
- `backend/app/services/experiment_service.py`
- `backend/app/api/system.py`

Behavior:

- Multiple repeated `GEMINI_API_KEY=...` lines in `.env` are now preserved and read.
- Backend status reports the count, for example: `gemini:gemini-3.5-flash (2 keys)`.
- Signup and experiment agents rotate Gemini keys.
- If a Gemini key hits quota/rate-limit/429/resource-exhausted, it is temporarily skipped and the next key is tried.

### 3. Lovable crawl failed on Windows

Original error in `backend/logs/backend.log`:

```text
playwright/_impl/_transport.py line 120
self._proc = await asyncio.create_subprocess_exec(...)
NotImplementedError
```

Cause:

- Running backend with `uvicorn --reload` on Windows caused Playwright/CloakBrowser to run under an event loop that did not support subprocess creation.

Fix:

- Restarted backend without `--reload`.
- Keep using `--loop asyncio`.

Verified:

- Lovable job #5 completed successfully:
  - `found=173`
  - `saved=173`

### 4. Lovable crawl was too slow

File changed:

- `backend/app/services/crawlers/lovable.py`

Problem:

- Lovable crawler extracted directory items, then synchronously resolved real homepages for every row.
- For Rewardful this meant resolving 173 domains, keeping the job in `running` for too long.

Fix:

- `resolve_homepages` now defaults to `False`.
- Crawl saves directory data quickly.
- Use the existing `discover-homepages` workflow later when needed.

### 5. Zombie crawl jobs

File changed:

- `backend/app/main.py`

Problem:

- If backend restarted while a crawl job was `running`, the job stayed stuck.

Fix:

- Startup recovery now marks `crawl_jobs.status == "running"` as:

```text
failed: Killed by server restart
```

Also fixed missing `datetime` import for that recovery path.

### 6. Logging Unicode error on Windows

File changed:

- `backend/app/core/logger.py`

Problem:

PowerShell log output raised:

```text
UnicodeEncodeError: 'charmap' codec can't encode character
```

Fix:

- Logger now reconfigures `sys.stdout` to UTF-8 with replacement fallback.

### 7. Gemini add-key button

Files changed:

- `backend/app/api/system.py`
- `frontend/app/(app)/library/page.tsx`

Problem:

- The Gemini key API worked for raw key values, but the UI flow was fragile and did not normalize values pasted from `.env`.
- Pasting `GEMINI_API_KEY=...` could be treated as the full key value instead of extracting the actual key.

Fix:

- Backend now normalizes raw keys, `GEMINI_API_KEY=...` lines, comma-separated keys, and multi-line pasted keys.
- Backend POST `/api/system/gemini-keys` can add multiple new keys in one request and skips duplicates.
- Frontend Gemini tab now uses a real form submit handler for both click and Enter.
- Frontend updates the React Query cache from the API response immediately after a successful add.

Verification:

```text
beforeTotal=2, afterAddTotal=4, afterDeleteTotal=2, envHasFake=False
```

### 8. Frontend stale `.next` static assets

Problem:

- `http://localhost:3001/library?tab=gemini` returned HTML `200`, but browser assets under `/_next/static/...` returned `404` or timed out.
- This happened after running `next build` while a dev server was still running, leaving the dev server and `.next` output out of sync.

Fix:

- Stop the process listening on port `3001`.
- Delete generated `frontend/.next`.
- Restart frontend with `npm.cmd run dev`.

Verification:

```text
GET /library?tab=gemini -> 200
GET /_next/static/css/app/layout.css?v=... -> 200
GET /_next/static/chunks/main-app.js?v=... -> 200
Frontend proxy add/delete Gemini key: 2 -> 3 -> 2, envHasFake=False
```

### 9. Gemini key selection for signup jobs

Files changed:

- `backend/app/services/llm_keyring.py`
- `backend/app/models/signup_job.py`
- `backend/app/core/db.py`
- `backend/app/api/signup.py`
- `backend/app/services/signup_runner.py`
- `backend/app/services/signup/agent_runner.py`
- `frontend/lib/api.ts`
- `frontend/app/(app)/signup/page.tsx`
- `frontend/app/(app)/library/page.tsx`

Fix:

- Gemini key add button on `/library?tab=gemini` now calls the add handler directly, accepts pasted `.env` lines, and shows inline API errors.
- Signup jobs now store `gemini_key_index` in `signup_jobs`.
- DB auto-migration adds `signup_jobs.gemini_key_index`.
- Signup API accepts/returns `gemini_key_index`.
- Signup page loads Gemini keys and lets the user choose auto-rotate or a specific key for the job.
- Agent tries the selected key first; if it hits quota/rate-limit, it still falls back to the rotating key pool.

Verification:

```text
Frontend proxy add/delete Gemini key: 2 -> 3 -> 2, envHasFake=False
signup_jobs has gemini_key_index column: True
POST /api/signup/jobs with gemini_key_index accepted by schema, returned business 400 for missing program/profile (not 422)
```

### 10. Gemini add-key UI direct async handler

File changed:

- `frontend/app/(app)/library/page.tsx`

Fix:

- The add-key button now uses a direct async handler instead of relying on the React Query mutation state for the click path.
- After adding a key, the UI sets the `gemini-keys` query data immediately, invalidates status, and shows an inline success message such as `Da them key. Hien co N key.`
- Inline errors are still shown below the input.

Verification:

```text
Playwright browser test:
before: 2 key
after: 3 key
success text visible: True
cleanup: total=2, envHasFake=False
```

## Useful Checks

Check ports:

```powershell
Get-NetTCPConnection -LocalPort 3001,8088 -ErrorAction SilentlyContinue |
  Where-Object State -eq Listen |
  Select-Object LocalPort,State,OwningProcess,
    @{Name='ProcessName';Expression={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}}
```

Check backend health:

```powershell
Invoke-WebRequest -UseBasicParsing http://localhost:8088/health
```

Tail backend log:

```powershell
Get-Content "D:\AI-PROJECT HUNTER\Browser_automation-main\backend\logs\backend.log" -Tail 100 -Wait
```

Run syntax check:

```powershell
cd "D:\AI-PROJECT HUNTER\Browser_automation-main\backend"
.\.venv\Scripts\python.exe -m py_compile app\main.py app\core\config.py app\core\logger.py app\services\llm_keyring.py app\services\crawlers\lovable.py
```

## Current Known Good State

- Backend: running on port `8088`
- Frontend: running on port `3001`
- LLM status: Gemini enabled with 2 keys
- CapSolver: enabled
- Lovable Rewardful crawl: verified success with 173 saved rows
