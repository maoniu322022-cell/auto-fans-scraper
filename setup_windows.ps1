param([string]$RepoDir = "$HOME\auto-fans-scraper")
Set-Location $RepoDir
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
playwright install
if (!(Test-Path .env)) {
@""@ | Out-Null
@"
LOG_LEVEL=INFO
LOG_FILE=logs/run.log
INPUT_FILE=data/names.txt
OUTPUT_CSV=results/phones.csv
"@ | Out-File -Encoding utf8 .env
}
Write-Host "Done. Run: python -u .\main.py"
