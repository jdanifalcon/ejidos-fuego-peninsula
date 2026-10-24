"""Validación y limpieza de la capa descargada.

Cada verificación devuelve hallazgos concretos (qué registro y por qué) que luego
alimentan el reporte de calidad. La regla general es *no borrar ni corregir en
silencio*: lo que se repara queda marcado en columnas del propio dataset.

Verificaciones:
- CRS: se asegura EPSG:4326 (y se calcula el área en EPSG:6372 y geodésica).
- Geometrías nulas, vacías e inválidas (se reparan con `make_valid`).
- Duplicados por clave (ID_ANP) y por geometría.
- Campos nulos o en blanco.
- Consistencia del campo ESTADOS (texto libre, abreviaturas, variantes).
- Superficie decretada vs. superficie calculada.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely

from ..config import Settings
from .geometry import MEXICO_LCC, WGS84, fold as _fold, geodesic_area_ha, records as _records, to_multipolygon

log = logging.getLogger(__name__)


ID_FIELD = "id_anp"
NAME_FIELD = "nombre"

# Campos que el servidor calcula en Web Mercator (EPSG:3857); no sirven como área real.
DROP_COLUMNS = ["shape__area", "shape__length"]
DATE_COLUMNS = ["prim_dec", "ult_dof", "pm"]

CATEGORIES = {
    "APFF": "Área de Protección de Flora y Fauna",
    "APRN": "Área de Protección de Recursos Naturales",
    "MN": "Monumento Natural",
    "PN": "Parque Nacional",
    "RB": "Reserva de la Biosfera",
    "SANT": "Santuario",
}

MEXICO_STATES = (
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche", "Chiapas", "Chihuahua",
    "Ciudad de México", "Coahuila", "Colima", "Durango", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco",
    "México", "Michoacán", "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla", "Querétaro",
    "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala",
    "Veracruz", "Yucatán", "Zacatecas",
)  # fmt: skip

# Variantes vistas en la fuente -> nombre oficial.
# "Estado de México" es la forma usual y no se marca; las abreviaturas sí.
STATE_ALIASES = {
    "b.c.": "Baja California",
    "b.c.s.": "Baja California Sur",
    "estado de mexico": "México",
    "edo. de mexico": "México",
    "cdmx": "Ciudad de México",
    "distrito federal": "Ciudad de México",
}


@dataclass
class ValidationReport:
    """Resumen de la validación; se serializa a JSON y se usa en el reporte."""

    total_input: int = 0
    total_output: int = 0
    crs_input: str | None = None
    crs_output: str = WGS84
    null_geometries: list[dict[str, Any]] = field(default_factory=list)
    invalid_geometries: list[dict[str, Any]] = field(default_factory=list)
    duplicate_ids: list[dict[str, Any]] = field(default_factory=list)
    duplicate_geometries: list[dict[str, Any]] = field(default_factory=list)
    null_counts: dict[str, int] = field(default_factory=dict)
    estados_issues: list[dict[str, Any]] = field(default_factory=list)
    area_discrepancies: list[dict[str, Any]] = field(default_factory=list)
    area_6372_vs_geodesic_pct: dict[str, float] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    peninsula_count: int = 0
    peninsula_states: list[str] = field(default_factory=list)
    area_tolerance_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


_STATE_LOOKUP = {_fold(s): s for s in MEXICO_STATES} | {_fold(k): v for k, v in STATE_ALIASES.items()}
_ALIAS_KEYS = {_fold(k) for k in STATE_ALIASES}
_ABBREVIATIONS = {_fold(k) for k in STATE_ALIASES if k != "estado de mexico"}


def _split_estados(value: str | None) -> list[str]:
    """'B.C., B.C.S. y Sonora' -> ['B.C.', 'B.C.S.', 'Sonora']."""
    if value is None:
        return []
    return [t.strip() for t in re.split(r"\s*[,;]\s*|\s+y\s+", str(value).strip()) if t.strip()]


def parse_estados(value: str | None) -> tuple[list[str], list[str]]:
    """Separa un texto como 'Campeche y Yucatán' o 'B.C., B.C.S. y Sonora'.

    Devuelve (entidades reconocidas en orden alfabético, fragmentos no reconocidos).
    """
    states, unknown = set(), []
    for token in _split_estados(value):
        match = _STATE_LOOKUP.get(_fold(token))
        if match:
            states.add(match)
        else:
            unknown.append(token)
    return sorted(states, key=_fold), unknown


# ---------------------------------------------------------------------------
# Verificaciones individuales
# ---------------------------------------------------------------------------


def normalize_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Nombres en minúsculas (evita identificadores entre comillas en PostgreSQL),
    se quitan campos calculados en 3857 y las fechas epoch-ms pasan a fecha."""
    gdf = gdf.rename(columns={c: c.lower() for c in gdf.columns if c != gdf.geometry.name})
    gdf = gdf.rename_geometry("geom")
    gdf = gdf.drop(columns=[c for c in DROP_COLUMNS if c in gdf.columns])
    for col in DATE_COLUMNS:
        if col in gdf.columns and pd.api.types.is_numeric_dtype(gdf[col]):
            gdf[col] = pd.to_datetime(gdf[col], unit="ms", utc=True).dt.date
    for col in gdf.select_dtypes(include="object").columns:
        if col != "geom":
            gdf[col] = gdf[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    return gdf


def ensure_crs(gdf: gpd.GeoDataFrame, report: ValidationReport) -> gpd.GeoDataFrame:
    """GeoJSON sin CRS declarado es WGS84 por especificación; cualquier otro se reproyecta."""
    report.crs_input = gdf.crs.to_string() if gdf.crs else None
    if gdf.crs is None:
        log.warning("La capa no declara CRS; se asume %s (RFC 7946)", WGS84)
        return gdf.set_crs(WGS84)
    if gdf.crs.to_epsg() != 4326:
        log.info("Reproyectando de %s a %s", gdf.crs, WGS84)
        return gdf.to_crs(WGS84)
    return gdf


def check_null_geometries(gdf: gpd.GeoDataFrame, report: ValidationReport) -> gpd.GeoDataFrame:
    mask = gdf.geometry.isna() | gdf.geometry.is_empty
    report.null_geometries = _records(gdf[mask], [ID_FIELD, NAME_FIELD])
    if mask.any():
        log.warning("Se descartan %d registros sin geometría", int(mask.sum()))
    return gdf[~mask].copy()


def fix_invalid_geometries(gdf: gpd.GeoDataFrame, report: ValidationReport) -> gpd.GeoDataFrame:
    """Repara con `shapely.make_valid` y deja rastro: `geom_reparada` y `motivo_invalidez`."""
    geoms = gdf.geometry.values
    invalid = ~shapely.is_valid(geoms)
    gdf["geom_reparada"] = invalid
    gdf["motivo_invalidez"] = None
    if invalid.any():
        gdf.loc[invalid, "motivo_invalidez"] = shapely.is_valid_reason(geoms[invalid])
        repaired = shapely.make_valid(geoms[invalid])
        gdf.loc[invalid, "geom"] = [to_multipolygon(g) for g in repaired]
    # Todas a MultiPolygon: un solo tipo de geometría para la columna de PostGIS.
    gdf["geom"] = [to_multipolygon(g) for g in gdf.geometry.values]
    gdf = gdf.set_geometry("geom", crs=WGS84)

    still_invalid = ~gdf.geometry.is_valid
    rows = gdf[invalid].assign(valida_despues=~still_invalid[invalid])
    report.invalid_geometries = _records(rows, [ID_FIELD, NAME_FIELD, "motivo_invalidez", "valida_despues"])
    log.info("Geometrías inválidas: %d (reparadas: %d)", int(invalid.sum()), int(rows["valida_despues"].sum()))
    return gdf


def check_duplicates(gdf: gpd.GeoDataFrame, report: ValidationReport) -> gpd.GeoDataFrame:
    """Duplicados por clave y por geometría idéntica (WKB de la geometría normalizada).
    Se conservan para el reporte; solo se eliminan duplicados *exactos* de clave."""
    dup_id = gdf[ID_FIELD].duplicated(keep=False)
    report.duplicate_ids = _records(gdf[dup_id], [ID_FIELD, NAME_FIELD])

    wkb = shapely.to_wkb(shapely.normalize(gdf.geometry.values))
    dup_geom = pd.Series(wkb, index=gdf.index).duplicated(keep=False)
    report.duplicate_geometries = _records(gdf[dup_geom], [ID_FIELD, NAME_FIELD])

    if dup_id.any():
        log.warning("Se conserva solo el primer registro de %d claves duplicadas", int(gdf[ID_FIELD].duplicated().sum()))
        gdf = gdf.drop_duplicates(subset=ID_FIELD, keep="first")
    return gdf


def check_nulls(gdf: gpd.GeoDataFrame, report: ValidationReport) -> None:
    """Cuenta nulos y cadenas vacías por campo de atributos."""
    counts = {}
    for col in gdf.columns.drop(["geom", "motivo_invalidez"], errors="ignore"):
        series = gdf[col]
        blank = series.map(lambda v: isinstance(v, str) and not v.strip())
        counts[col] = int(series.isna().sum() + blank.sum())
    report.null_counts = counts


def normalize_estados(gdf: gpd.GeoDataFrame, settings: Settings, report: ValidationReport) -> gpd.GeoDataFrame:
    """Agrega `estados_norm` (lista estandarizada) y `en_peninsula`; conserva `estados` original."""
    parsed = gdf["estados"].map(parse_estados)
    gdf["estados_norm"] = parsed.map(lambda p: "; ".join(p[0]) or None)
    gdf["estados_texto_libre"] = parsed.map(lambda p: bool(p[1]))
    peninsula = {_fold(s) for s in settings.peninsula_states}
    gdf["en_peninsula"] = parsed.map(lambda p: any(_fold(s) in peninsula for s in p[0]))

    # La misma combinación de entidades escrita de varias formas
    # (p. ej. "Campeche y Yucatán" / "Yucatán y Campeche") es una inconsistencia.
    # Se marca cada escritura que difiere de la más frecuente para esa combinación.
    most_common = gdf.groupby("estados_norm")["estados"].agg(lambda s: s.value_counts().index[0])

    issues = []
    for (_, row), (states, unknown) in zip(gdf.iterrows(), parsed):
        original = row["estados"] if isinstance(row["estados"], str) else ""
        tokens = _split_estados(original)
        reasons = []
        if not original:
            reasons.append("Campo vacío")
        if unknown and not states:
            reasons.append("Texto libre, sin entidad federativa reconocible")
        elif unknown:
            reasons.append(f"Fragmento no reconocido como entidad: «{', '.join(unknown)}»")
        if any(_fold(t) in _ABBREVIATIONS for t in tokens):
            reasons.append("Usa abreviaturas")
        if any(_fold(t) in _STATE_LOOKUP and _fold(t) not in _ALIAS_KEYS and t != _STATE_LOOKUP[_fold(t)] for t in tokens):
            reasons.append("Variante ortográfica (acentos/mayúsculas)")
        usual = most_common.get(row["estados_norm"])
        if usual and original != usual:
            reasons.append(f"Escrito distinto a la forma más común: «{usual}»")
        if reasons:
            issues.append(
                {ID_FIELD: row[ID_FIELD], NAME_FIELD: row[NAME_FIELD], "estados": original,
                 "estados_norm": row["estados_norm"], "observacion": "; ".join(reasons)}  # fmt: skip
            )
    report.estados_issues = issues
    report.peninsula_count = int(gdf["en_peninsula"].sum())
    report.peninsula_states = list(settings.peninsula_states)
    return gdf


def compute_areas(gdf: gpd.GeoDataFrame, settings: Settings, report: ValidationReport) -> gpd.GeoDataFrame:
    """Compara la superficie decretada (ha) contra la calculada.

    - `area_ha_geodesica`: sobre el elipsoide; es la referencia.
    - `area_ha_6372`: en México LCC. LCC es *conforme*, no de áreas iguales, así que
      tiene un pequeño sesgo; se reporta para mostrar esa diferencia.
    """
    gdf["area_ha_geodesica"] = gdf.geometry.map(geodesic_area_ha).round(2)
    gdf["area_ha_6372"] = (gdf.geometry.to_crs(MEXICO_LCC).area / 10_000).round(2)
    gdf["dif_superficie_pct"] = ((gdf["area_ha_geodesica"] - gdf["superficie"]) / gdf["superficie"] * 100).round(2)

    lcc_bias = (gdf["area_ha_6372"] - gdf["area_ha_geodesica"]) / gdf["area_ha_geodesica"] * 100
    report.area_6372_vs_geodesic_pct = {
        "min": round(float(lcc_bias.min()), 3),
        "mediana": round(float(lcc_bias.median()), 3),
        "max": round(float(lcc_bias.max()), 3),
    }
    report.area_tolerance_pct = settings.area_tolerance_pct
    off = gdf[gdf["dif_superficie_pct"].abs() > settings.area_tolerance_pct]
    off = off.reindex(off["dif_superficie_pct"].abs().sort_values(ascending=False).index)
    report.area_discrepancies = _records(off, [ID_FIELD, NAME_FIELD, "superficie", "area_ha_geodesica", "dif_superficie_pct"])
    return gdf


def add_derived_columns(gdf: gpd.GeoDataFrame, report: ValidationReport) -> gpd.GeoDataFrame:
    gdf["categoria"] = gdf["cat_manejo"].map(CATEGORIES).fillna(gdf["cat_manejo"])
    gdf["tiene_programa_manejo"] = gdf["pm"].notna()
    report.category_counts = {k: int(v) for k, v in gdf["cat_manejo"].value_counts().items()}
    return gdf


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------


def validate(gdf: gpd.GeoDataFrame, settings: Settings) -> tuple[gpd.GeoDataFrame, ValidationReport]:
    """Aplica todas las verificaciones en orden y devuelve (datos limpios, reporte)."""
    report = ValidationReport(total_input=len(gdf))
    gdf = normalize_columns(gdf)
    gdf = ensure_crs(gdf, report)
    gdf = check_null_geometries(gdf, report)
    gdf = fix_invalid_geometries(gdf, report)
    gdf = check_duplicates(gdf, report)
    check_nulls(gdf, report)
    gdf = normalize_estados(gdf, settings, report)
    gdf = compute_areas(gdf, settings, report)
    gdf = add_derived_columns(gdf, report)
    report.total_output = len(gdf)
    log.info("Validación: %d entrada -> %d salida", report.total_input, report.total_output)
    return gdf.reset_index(drop=True), report
