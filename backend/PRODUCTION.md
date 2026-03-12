# Production Deployment Guide

## 1) Prepare environment
1. Create virtual environment and install dependencies:
   - `python -m venv .venv`
   - `.venv\\Scripts\\pip install -r requirements.txt`
2. Copy `.env.production.example` to `.env.production` and update values.
3. Set a strong `SECRET_KEY` and real DB/reader values.
4. Keep `APP_ENV=production`.
5. This build is configured for MSSQL. Set `DB_SERVER`, `DB_NAME`, auth mode, and encryption flags.

## 2) Start server
- PowerShell command:
  - `./start_production.ps1 -Workers 1`
- Dashboard URL:
  - `http://<server-ip>:<APP_PORT>/login`

## 3) Health checks
- Liveness: `GET /health/live`
- Readiness: `GET /health/ready`

## 4) Security checklist
- Use HTTPS in front of app (Nginx/IIS reverse proxy).
- Set `COOKIE_SECURE=true` on HTTPS.
- Restrict `CORS_ALLOW_ORIGINS` to real dashboard domain.
- Set `RFID_ACTIVE_PUSH_TOKEN` and keep it secret.
- Rotate bootstrap admin password after first login.

## 5) Changing config later
- Yes, you can update `.env.production` anytime.
- Restart service after config changes to apply new values.
- No code change is required for regular ops settings.
