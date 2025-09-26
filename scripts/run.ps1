# PowerShell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install
python .\scrape.py
