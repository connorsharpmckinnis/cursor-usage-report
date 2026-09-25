# Cursor / Grok Bot usage report

Upload a Cursor **Team** usage export CSV and get:

1. Interactive Plotly dashboard (Overview · Cursor IDE · Grok Bot · Compare)
2. **Copy AI analysis pack** — markdown tables for every chart grouping, so you can paste into an AI chat and compare trends across views

Team plan has no usage API for this export. Flow: Cursor dashboard → download CSV → upload here.

## Docker Desktop

```bash
docker compose up --build -d
open http://localhost:9003
```

Stop:

```bash
docker compose down
```

Uploads persist in the `usage-report-data` Docker volume.

## CLI

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python generate_report.py path/to/team-usage-events.csv
open report.html
```

## Local web (no Docker)

```bash
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 9003
```

## Server deploy (e.g. apex-box behind firewall)

```bash
docker compose up --build -d
```

Point staff at `http://<host>:9003` (or put nginx/Caddy in front if you already terminate TLS there). No outbound Cursor API needed; uploaded CSVs stay on the host. The AI pack only leaves the host if someone copies it into an external chat.

Optional env:

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `9003` | Listen port |
| `USAGE_REPORT_DATA` | `/data/uploads` | Session storage |
| `USAGE_REPORT_MAX_UPLOAD` | `41943040` | Max CSV bytes (40 MiB) |

## Seat assumptions

Team licenses are not in the CSV. Defaults are editable on the upload form (seat cost + seat emails).

## Privacy

Do not commit usage export CSVs. They are gitignored (`*.csv`).
