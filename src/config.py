"""Configuración central del pipeline.

Todo lo que cambia entre entornos (capa de ArcGIS, estados, periodo, umbrales,
proyecto de Earth Engine, credenciales de la base) se lee de variables de entorno;
`python-dotenv` carga el archivo `.env` si existe. Ampliar el análisis a otro
estado o periodo es cuestión de editar `.env`, no el código.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_LAYER_URL = (
    "https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/"
    "CONAMP_Federal_Protected_Areas_in_Mexico_2026/FeatureServer/1"
)


@dataclass(frozen=True)
class State:
    """Entidad federativa: código corto (el de los archivos del RAN), nombre y clave INEGI."""

    code: str
    name: str
    inegi: int


STATES = {
    s.code: s
    for s in (
        State("camp", "Campeche", 4),
        State("chis", "Chiapas", 7),
        State("qroo", "Quintana Roo", 23),
        State("tab", "Tabasco", 27),
        State("ver", "Veracruz", 30),
        State("yuc", "Yucatán", 31),
    )
}

# Identificadores SQL simples: evitan inyección al interpolar esquema/tabla en DDL.
_SQL_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _sql_identifier(name: str, value: str) -> str:
    if not _SQL_IDENTIFIER.match(value):
        raise ValueError(f"{name}={value!r} no es un identificador SQL válido (usa minúsculas, dígitos y _)")
    return value


def _parse_years(value: str) -> tuple[int, int]:
    """'2015-2024' -> (2015, 2024)."""
    start, _, end = value.partition("-")
    first, last = int(start), int(end or start)
    if not 2001 <= first <= last:
        raise ValueError(f"YEARS={value!r} inválido; usa AAAA-AAAA a partir de 2001")
    return first, last


def _parse_states(value: str) -> tuple[State, ...]:
    codes = [c.strip().lower() for c in value.split(",") if c.strip()]
    unknown = [c for c in codes if c not in STATES]
    if unknown:
        raise ValueError(f"Estados desconocidos en STATES: {unknown}. Opciones: {sorted(STATES)}")
    return tuple(STATES[c] for c in codes)


@dataclass(frozen=True)
class Settings:
    """Parámetros del pipeline. Se construye con `get_settings()`."""

    # --- Contexto: ArcGIS FeatureServer (ANP) ---
    layer_url: str
    where: str
    page_size: int
    request_timeout: int
    dataset_name: str
    peninsula_states: tuple[str, ...]
    area_tolerance_pct: float
    simplify_tolerance_m: float

    # --- Análisis: ejidos, periodo y Earth Engine ---
    states: tuple[State, ...]
    years: tuple[int, int]
    treecover_threshold: int
    fire_buffer_sens_m: int
    ee_project: str | None
    hansen_asset: str
    firms_min_confidence: int
    gee_batch_size: int
    gee_workers: int
    overlap_min_ha: float

    # --- PostGIS ---
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str | None
    db_schema: str
    db_table: str

    # --- Rutas ---
    data_dir: Path
    reports_dir: Path
    web_dir: Path
    docs_dir: Path

    # Rutas de la capa de contexto (ANP)
    @property
    def raw_geojson_path(self) -> Path:
        return self.data_dir / "raw" / f"{self.dataset_name}.geojson"

    @property
    def raw_metadata_path(self) -> Path:
        return self.data_dir / "raw" / f"{self.dataset_name}_metadata.json"

    @property
    def processed_gpkg_path(self) -> Path:
        return self.data_dir / "processed" / f"{self.dataset_name}_limpio.gpkg"

    @property
    def validation_json_path(self) -> Path:
        return self.data_dir / "processed" / "validacion.json"

    # Rutas del análisis
    @property
    def ran_dir(self) -> Path:
        return self.data_dir / "ran"

    @property
    def gee_dir(self) -> Path:
        return self.data_dir / "gee"

    @property
    def ejidos_gpkg_path(self) -> Path:
        return self.data_dir / "processed" / "ejidos_limpio.gpkg"

    @property
    def web_data_dir(self) -> Path:
        return self.web_dir / "data"

    @property
    def hansen_version(self) -> str:
        """'UMD/hansen/global_forest_change_2025_v1_13' -> 'v1_13'."""
        return self.hansen_asset.rsplit("_", 2)[-2] + "_" + self.hansen_asset.rsplit("_", 1)[-1]

    @property
    def database_url(self) -> URL:
        """URL de SQLAlchemy. `URL.create` escapa caracteres especiales de la contraseña."""
        if not self.db_password:
            raise RuntimeError("Falta POSTGRES_PASSWORD. Copia .env.example a .env y define una contraseña.")
        return URL.create(
            "postgresql+psycopg2",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


def get_settings() -> Settings:
    """Lee las variables de entorno y aplica valores por defecto razonables."""
    peninsula = os.getenv("PENINSULA_STATES", "Campeche,Quintana Roo,Yucatán")
    return Settings(
        layer_url=os.getenv("ARCGIS_LAYER_URL", DEFAULT_LAYER_URL).rstrip("/"),
        where=os.getenv("ARCGIS_WHERE", "1=1"),
        page_size=_env_int("ARCGIS_PAGE_SIZE", 50),
        request_timeout=_env_int("ARCGIS_TIMEOUT", 60),
        dataset_name=os.getenv("DATASET_NAME", "anp_conanp_2026"),
        peninsula_states=tuple(s.strip() for s in peninsula.split(",") if s.strip()),
        area_tolerance_pct=_env_float("AREA_TOLERANCE_PCT", 10.0),
        simplify_tolerance_m=_env_float("WEB_SIMPLIFY_TOLERANCE_M", 150.0),
        states=_parse_states(os.getenv("STATES", "camp,tab")),
        years=_parse_years(os.getenv("YEARS", "2015-2024")),
        treecover_threshold=_env_int("TREECOVER_THRESHOLD", 30),
        fire_buffer_sens_m=_env_int("FIRE_BUFFER_SENS_M", 500),
        ee_project=os.getenv("EE_PROJECT") or None,
        hansen_asset=os.getenv("HANSEN_ASSET", "UMD/hansen/global_forest_change_2025_v1_13"),
        firms_min_confidence=_env_int("FIRMS_MIN_CONFIDENCE", 50),
        gee_batch_size=_env_int("GEE_BATCH_SIZE", 5),
        gee_workers=_env_int("GEE_WORKERS", 2),
        overlap_min_ha=_env_float("OVERLAP_MIN_HA", 1.0),
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=_env_int("POSTGRES_PORT", 5433),
        db_name=os.getenv("POSTGRES_DB", "geodata"),
        db_user=os.getenv("POSTGRES_USER", "gis"),
        db_password=os.getenv("POSTGRES_PASSWORD"),
        db_schema=_sql_identifier("POSTGRES_SCHEMA", os.getenv("POSTGRES_SCHEMA", "conanp")),
        db_table=_sql_identifier("POSTGRES_TABLE", os.getenv("POSTGRES_TABLE", "anp_federales")),
        data_dir=PROJECT_ROOT / "data",
        reports_dir=PROJECT_ROOT / "reports",
        web_dir=PROJECT_ROOT / "web",
        docs_dir=PROJECT_ROOT / "docs",
    )
