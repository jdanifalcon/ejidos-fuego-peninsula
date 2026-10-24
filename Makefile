# Atajos del proyecto (Linux / macOS / WSL / Git Bash con make).
# En Windows PowerShell usa el equivalente: .\run.ps1 <tarea>

PY ?= python3
VENV := .venv
ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
else
  BIN := $(VENV)/bin
endif

.PHONY: all setup env up run run-cache run-nodb test serve psql down clean

all: setup up run  ## Todo: entorno, PostGIS y pipeline

setup: env  ## Crea el entorno virtual e instala dependencias
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -r requirements.txt

env:  ## Crea .env a partir de .env.example si no existe
	@test -f .env || (cp .env.example .env && echo "Creado .env: cambia POSTGRES_PASSWORD")

up: env  ## Levanta PostGIS y espera a que esté sano
	docker compose up -d --wait

run:  ## Ejecuta el pipeline completo
	$(BIN)/python -m src.pipeline

run-cache:  ## Pipeline sin llamar a Earth Engine (usa data/gee/)
	$(BIN)/python -m src.pipeline --skip-gee

run-nodb:  ## Pipeline sin PostGIS
	$(BIN)/python -m src.pipeline --skip-db

test:  ## Pruebas unitarias (no requieren red ni base de datos)
	$(BIN)/python -m pytest -q

serve:  ## Sirve el mapa en http://localhost:8000
	$(BIN)/python -m http.server -d web 8000

psql:  ## Consola SQL dentro del contenedor
	docker compose exec postgis sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB'

down:  ## Detiene PostGIS (conserva los datos)
	docker compose down

clean:  ## Borra datos intermedios y el volumen de PostGIS
	docker compose down -v
	rm -rf data/raw data/processed
