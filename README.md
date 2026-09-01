# lifeOS

Personal data-fusion system. This slice: one-way sync of Google Calendar events into a Notion database, via a Postgres-backed FastAPI service running in Docker Compose.

## One-time setup

1. **Install Docker Desktop** if you haven't already.

2. **Google Cloud Console**
   - Create/select a project, enable the "Google Calendar API".
   - Configure the OAuth consent screen: type "External", add yourself as a test user (keeps it in "Testing" status, avoiding Google's verification process).
   - Create OAuth Client ID credentials, type "Desktop app".
   - Download the JSON, save it as `secrets/google_client_secret.json`.

3. **Authorize Google Calendar access (run on your host, not in Docker)**
   ```bash
   cd app
   pip install -r requirements.txt   # or use a venv
   python3 -m services.google_auth --authorize \
     --client-secret ../secrets/google_client_secret.json \
     --token-path ../secrets/google_token.json
   ```
   This opens a browser for consent and writes `secrets/google_token.json`, which the container mounts read-only afterwards.

4. **Notion**
   - Create an internal integration at https://www.notion.so/my-integrations, copy the token.
   - Create a database with these properties: `Title` (title), `Start` (date), `Location` (rich text), `Source` (select), `External ID` (rich text), `Calendar` (rich text), `Last Synced` (date).
   - Share/connect the database with your integration.
   - Put the token and database ID into `.env` as `NOTION_TOKEN` and `NOTION_DATABASE_ID`.

5. **A+ (optional, plus.cs.aalto.fi assignment deadlines)**
   - Grab your personal API token from https://plus.cs.aalto.fi/accounts/accounts/ (the "API token" field — a single hex string).
   - Save it as plain text in `secrets/aplus_token` (no `Token ` prefix, no newline needed).
   - Set `APLUS_ENABLED=true` in `.env`.
   - Deadlines are pulled at the course-module level for every course A+ reports you're enrolled in, and mirrored into the Assignments database (auto-linked to a Course row when the code matches one from MyCourses).

6. **DigiCampus (optional, University of Helsinki assignment deadlines)**
   - In DigiCampus (https://digicampus.fi) go to Calendar → Export, pick "Events related to courses" and "Recent and next 60 days", and copy the calendar URL (it carries a personal `authtoken`).
   - Put it into `.env` as `DIGICAMPUS_ICAL_URL`.
   - Moodle exports each assignment as several timed events (submission opens/closes, peer-review opens, grading period ends); only genuine cut-offs are kept, matched by Finnish deadline wording, and mirrored into the Assignments database. The feed only carries the course shortname (no name), so these link to a Course row only if one already exists for that code.

7. **Environment**
   ```bash
   cp .env.example .env   # already done if you're reading this after initial scaffolding
   ```
   Fill in `NOTION_TOKEN`, `NOTION_DATABASE_ID`, and change `POSTGRES_PASSWORD` from the placeholder.

## Running

```bash
docker compose up --build
```

Trigger a sync manually:
```bash
curl -X POST http://localhost:8000/sync/calendar
curl http://localhost:8000/sync/status
```

Automatic sync also runs every `SYNC_INTERVAL_MINUTES` (default 15) once the container is up.

## Database migrations

```bash
cd app
alembic upgrade head
```
Run this after `docker compose up -d postgres` and before starting the app for the first time.
