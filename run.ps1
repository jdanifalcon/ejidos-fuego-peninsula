# Equivalente al Makefile para Windows PowerShell.
# Uso: .\run.ps1 all | setup | up | run | run-nodb | test | serve | psql | down | clean
param([Parameter(Position = 0)][string]$Task = "all")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"   # acentos correctos en la consola de Windows
$Py = ".\.venv\Scripts\python.exe"

function Invoke-Checked([scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Block" }
}

function Setup {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Creado .env: cambia POSTGRES_PASSWORD" }
    if (-not (Test-Path $Py)) { Invoke-Checked { py -3 -m venv .venv } }
    Invoke-Checked { & $Py -m pip install --upgrade pip }
    Invoke-Checked { & $Py -m pip install -r requirements.txt }
}

function Up { Invoke-Checked { docker compose up -d --wait } }
function Run { Invoke-Checked { & $Py -m src.pipeline } }

switch ($Task) {
    "all"      { Setup; Up; Run }
    "setup"    { Setup }
    "up"       { Up }
    "run"      { Run }
    "run-cache" { Invoke-Checked { & $Py -m src.pipeline --skip-gee } }
    "run-nodb" { Invoke-Checked { & $Py -m src.pipeline --skip-db } }
    "test"     { Invoke-Checked { & $Py -m pytest -q } }
    "serve"    { & $Py -m http.server -d web 8000 }
    "psql"     { docker compose exec postgis sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB' }
    "down"     { docker compose down }
    "clean"    { docker compose down -v; Remove-Item -Recurse -Force data\raw, data\processed -ErrorAction SilentlyContinue }
    default    { Write-Error "Tarea desconocida: $Task" }
}
