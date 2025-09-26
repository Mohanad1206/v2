# Gaming Accessories Scraper — **Rooted Paths + Text Output**

Everything is resolved from the **project root**, so you can run the script from anywhere and it will still read:
- `sites.txt`
- `config.yaml`
- write to `output/`
- log to `logs/`

## Quick Start

```bash
./scripts/run.sh
```
or on Windows PowerShell:
```powershell
./scripts/run.ps1
```

### Custom options (all relative to project root)
```bash
python scrape.py --first-n 50 --dynamic auto --sites sites.txt --out-dir output
python scrape.py --dynamic always
python scrape.py --static-only
```

### Output (text, not CSV)
`output/YYYYmmdd_HHMMSS_scrape.txt` with lines:
```
timestamp_iso | site_name | product_name | status | price_value | currency | product_url | raw_price_text
```

### Files & Folders
```
gaming_scraper_text_roots/
├─ scrape.py                 # rooted CLI launcher
├─ scraper_pkg/
│  ├─ __init__.py
│  └─ scraper.py            # main logic (rooted paths)
├─ sites.txt
├─ config.yaml
├─ requirements.txt
├─ .env.example
├─ output/
├─ logs/
└─ scripts/
   ├─ run.sh
   └─ run.ps1
```


---

## GitHub Setup (CI)

This repo includes a GitHub Actions workflow: **.github/workflows/scrape.yml**.

### What it does
- Runs daily on a cron (02:23 UTC) and on **Run workflow** (manual).
- Installs Python + Playwright, runs `python scrape.py` (rooted).
- Uploads the generated `.txt` to the workflow **Artifacts**.
- Commits `output/*.txt` back to the repo (uses `GITHUB_TOKEN`).
- Optionally POSTs to a **Make** webhook if `MAKE_WEBHOOK_URL` secret is set.

### Configure variables / secrets
- **Repository variables** (Settings → Variables → Actions → New variable):
  - `SCRAPER_FIRST_N` (default: `50`)
  - `SCRAPER_DYNAMIC` (default: `auto`)

- **Repository secrets** (Settings → Secrets and variables → Actions → New secret):
  - `MAKE_WEBHOOK_URL` (optional) — your Make webhook URL

### Trigger manually
Go to **Actions → Scrape Gaming Sites (Text) → Run workflow**.

### Notes
- Outputs are committed under `output/`. If you prefer not to commit them, remove the "Commit & push" step in the workflow.
- The scraper writes logs to `logs/scrape.log` (ignored by git).
