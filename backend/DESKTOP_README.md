# Desktop Wrapper (Windows)

This adds a desktop shell around the existing FastAPI + HTML/JS application without changing backend/frontend business logic.

## What was added

- `desktop_launcher.py`: starts existing backend (`uvicorn main:app`), waits for `/health/ready`, opens app in `pywebview`, stops backend on close.
- `run_desktop.ps1`: local desktop-mode launcher.
- `build_desktop.ps1`: PyInstaller build script for Windows EXE.
- `requirements-desktop.txt`: desktop tooling dependencies only.

## What was not changed

- No route/endpoint rename or removal.
- No auth/session logic changes.
- No WebSocket schema changes.
- No RFID ingest/processing changes.
- No DB model/schema/business logic changes.
- Existing web mode remains runnable as-is.

## Local desktop run

From `backend`:

```powershell
# 1) Install desktop dependencies
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt

# 2) Run desktop mode
.\run_desktop.ps1
```

If `.venv` is not present:

```powershell
python -m pip install -r requirements-desktop.txt
python desktop_launcher.py
```

## Smoke check (no UI)

This runs backend start/readiness + auth/websocket/api checks:

```powershell
.\run_desktop.ps1 -Smoke
```

It uses credentials from `.env.production` (bootstrap admin) by default:
- `BOOTSTRAP_ADMIN_USERNAME`
- `BOOTSTRAP_ADMIN_PASSWORD`

You can override with:
- `DESKTOP_SMOKE_USERNAME`
- `DESKTOP_SMOKE_PASSWORD`

## Build Windows EXE

From `backend`:

```powershell
.\build_desktop.ps1
```

Custom EXE name:

```powershell
.\build_desktop.ps1 -Name "KolJewelleryDesktop"
```

Output:
- `backend\dist\KolJewelleryDesktop.exe` (or custom name)

## Notes for deployment

- The EXE wraps the existing backend and expects the existing backend project files (`main.py`, `static`, `.env.production`, DB/rfid/runtime files) to remain available.
- Existing browser-based web mode is unchanged. You can still run:

```powershell
.\run_backend.bat
```
