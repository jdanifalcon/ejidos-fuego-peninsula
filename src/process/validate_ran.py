"""Validación y limpieza de los núcleos agrarios (ejidos) del RAN.

Problemas reales encontrados en los KML de datos abiertos y cómo se tratan:

- Coordenadas con Z (siempre 0) -> se pasan a 2D.
- Geometrías autointersectadas -> `make_valid`, marcadas con `geom_reparada`.
- `CLAVE` repetida:
    * partes de un mismo ejido sin traslape (p. ej. HOLBOX en tres polígonos) -> se unen;
    * el mismo ejido registrado dos veces con programas distintos (p. ej. CHICOACAN en
      PROCEDE y en FANAR, con polígonos casi idénticos) -> se conserva el registro del
      programa más reciente (FANAR) y se documenta.
- Ejidos distintos que se traslapan -> no se recortan (no sabemos cuál límite es el
  correcto); se registran en `traslapes` y los totales estatales se calculan sobre la
  unión de los ejidos para no contar dos veces la misma superficie.
- Variantes de texto ("HECELCHAKAN" / "HECELCHAKÁN", "Procede" / "PROCEDE") ->
  columnas `*_norm`; el valor original se conserva.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely

from ..config import Settings
from .geometry import MEXICO_LCC, WGS84, fold, geodesic_area_ha, records, to_multipolygon

log = logging.getLogger(__name__)

KEY = "clave"
# Cuando un mismo ejido aparece en dos programas se conserva el más reciente:
# FANAR (Fondo de Apoyo para Núcleos Agrarios sin Regularizar) sustituyó a PROCEDE en 2007.
PROGRAM_PRIORITY = {"FANAR": 3, "DIVISION": 2, "PROCEDE": 1}
DUPLICATE_OVERLAP_RATIO = 0.5  # traslape (sobre el polígono menor) para considerarlo re-registro
SMALL_POLYGON_HA = 5.0


@dataclass
class RanValidationReport:
    """Resumen de la validación de ejidos; alimenta el reporte de calidad."""

    per_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    crs_input: str | None = None
    null_geometries: list[dict[str, Any]] = field(default_factory=list)
    invalid_geometries: list[dict[str, Any]] = field(default_factory=list)
    duplicate_keys: list[dict[str, Any]] = field(default_factory=list)
    overlaps_summary: dict[str, dict[str, float]] = field(default_factory=dict)
    top_overlaps: list[dict[str, Any]] = field(default_factory=list)
    text_normalizations: list[dict[str, Any]] = field(default_factory=list)
    small_polygons: list[dict[str, Any]] = field(default_factory=list)
    null_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Verificaciones
# ---------------------------------------------------------------------------


def ensure_crs_2d(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    report.crs_input = gdf.crs.to_string() if gdf.crs else None
    gdf = gdf.set_crs(WGS84) if gdf.crs is None else gdf.to_crs(WGS84)
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)
    return gdf.rename_geometry("geom")


def drop_null_geometries(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    mask = gdf.geometry.isna() | gdf.geometry.is_empty
    report.null_geometries = records(gdf[mask], ["estado", KEY, "nombre"])
    return gdf[~mask].copy()


def fix_invalid(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    geoms = gdf.geometry.values
    invalid = ~shapely.is_valid(geoms)
    gdf["geom_reparada"] = invalid
    gdf["motivo_invalidez"] = None
    if invalid.any():
        gdf.loc[invalid, "motivo_invalidez"] = shapely.is_valid_reason(geoms[invalid])
        gdf.loc[invalid, "geom"] = [to_multipolygon(g) for g in shapely.make_valid(geoms[invalid])]
    gdf["geom"] = [to_multipolygon(g) for g in gdf.geometry.values]
    gdf = gdf.set_geometry("geom", crs=WGS84)
    rows = gdf[invalid].assign(valida_despues=gdf.geometry[invalid].is_valid)
    report.invalid_geometries = records(rows, ["estado", KEY, "nombre", "motivo_invalidez", "valida_despues"])
    log.info("Ejidos: %d geometrías inválidas reparadas", int(invalid.sum()))
    return gdf


def normalize_text(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    """`programa_norm` en mayúsculas; `municipio_norm` toma, por estado, la variante
    con acentos (o la más frecuente) entre las que solo difieren en acentos/mayúsculas."""
    for col in ("nombre", "municipio", "programa", "tipo"):
        gdf[col] = gdf[col].map(lambda v: " ".join(v.split()) if isinstance(v, str) else v)
    gdf["programa_norm"] = gdf["programa"].str.upper()

    def pick(variants: pd.Series) -> str:
        counts = variants.value_counts()
        accented = [v for v in counts.index if v != fold(v).upper()]
        return accented[0] if accented else counts.index[0]

    key = gdf["estado"] + "|" + gdf["municipio"].fillna("").map(fold)
    canonical = gdf.groupby(key)["municipio"].agg(pick)
    gdf["municipio_norm"] = key.map(canonical)

    changes = []
    for col, norm in (("municipio", "municipio_norm"), ("programa", "programa_norm")):
        diff = gdf[gdf[col] != gdf[norm]].groupby([col, norm]).size()
        changes += [{"campo": col, "original": o, "normalizado": n, "registros": int(c)} for (o, n), c in diff.items()]
    report.text_normalizations = changes
    return gdf


def resolve_duplicate_keys(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    """Une las partes de un mismo ejido o descarta el registro más antiguo si es re-registro."""
    dup_keys = gdf.loc[gdf[KEY].duplicated(keep=False), KEY].unique()
    if not len(dup_keys):
        return gdf
    projected = gdf.geometry.to_crs(MEXICO_LCC)
    keep_rows, new_rows, notes = [], [], []
    for key in dup_keys:
        idx = gdf.index[gdf[KEY] == key]
        parts = projected.loc[idx]
        smaller = parts.area.min()
        overlap = shapely.intersection_all(parts.values).area if len(idx) == 2 else 0.0
        ratio = overlap / smaller if smaller else 0.0
        if len(idx) == 2 and ratio > DUPLICATE_OVERLAP_RATIO:
            priority = gdf.loc[idx, "programa_norm"].map(PROGRAM_PRIORITY).fillna(0)
            winner = priority.idxmax()
            keep_rows.append(winner)
            action = f"Re-registro: se conserva {gdf.at[winner, 'programa_norm']}"
        else:
            merged = gdf.loc[[idx[0]]].copy()
            merged["geom"] = [to_multipolygon(shapely.union_all(gdf.loc[idx].geometry.values))]
            merged["geom_reparada"] = bool(gdf.loc[idx, "geom_reparada"].any())
            new_rows.append(merged)
            action = f"Partes del mismo ejido: se unen {len(idx)} polígonos"
        notes.append({"estado": gdf.at[idx[0], "estado"], KEY: key, "nombre": gdf.at[idx[0], "nombre"],
                      "registros": len(idx), "programas": ", ".join(sorted(set(gdf.loc[idx, "programa_norm"].dropna()))),
                      "traslape_pct": round(ratio * 100, 1), "accion": action})  # fmt: skip
    report.duplicate_keys = notes
    untouched = gdf[~gdf[KEY].isin(dup_keys)]
    out = pd.concat([untouched, gdf.loc[keep_rows], *new_rows], ignore_index=True)
    log.info("Ejidos: %d claves duplicadas resueltas", len(dup_keys))
    return gpd.GeoDataFrame(out, geometry="geom", crs=WGS84)


def find_overlaps(gdf: gpd.GeoDataFrame, settings: Settings, report: RanValidationReport) -> gpd.GeoDataFrame:
    """Pares de ejidos distintos cuya intersección supera OVERLAP_MIN_HA."""
    projected = gdf.to_crs(MEXICO_LCC)
    left, right = projected.sindex.query(projected.geometry, predicate="intersects")
    pairs = [(i, j) for i, j in zip(left, right) if i < j]
    rows, slivers = [], 0
    for i, j in pairs:
        inter = projected.geometry.iat[i].intersection(projected.geometry.iat[j])
        area_ha = inter.area / 10_000
        if area_ha < settings.overlap_min_ha:
            # Astillas: límites compartidos digitalizados con pequeñas diferencias.
            slivers += area_ha > 0
            continue
        a, b = projected.iloc[i], projected.iloc[j]
        rows.append({
            "clave_a": a[KEY], "nombre_a": a["nombre"], "clave_b": b[KEY], "nombre_b": b["nombre"],
            "estado": a["estado"], "area_ha": round(area_ha, 2),
            "pct_a": round(100 * inter.area / a.geom.area, 2), "pct_b": round(100 * inter.area / b.geom.area, 2),
            "geom": to_multipolygon(inter),
        })  # fmt: skip
    columns = ["clave_a", "nombre_a", "clave_b", "nombre_b", "estado", "area_ha", "pct_a", "pct_b", "geom"]
    overlaps = gpd.GeoDataFrame(rows, columns=columns, geometry="geom", crs=MEXICO_LCC).to_crs(WGS84)
    report.overlaps_summary = {
        code: {"pares": int(len(g)), "area_ha": round(float(g["area_ha"].sum()), 1)}
        for code, g in overlaps.groupby("estado")
    }
    report.overlaps_summary["_astillas"] = {"pares": int(slivers), "umbral_ha": settings.overlap_min_ha}
    report.top_overlaps = records(overlaps.sort_values("area_ha", ascending=False).head(10), columns[:-1])
    log.info("Ejidos: %d pares de traslapes > %s ha", len(overlaps), settings.overlap_min_ha)
    return overlaps


def compute_areas(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> gpd.GeoDataFrame:
    gdf["area_ha"] = gdf.geometry.map(geodesic_area_ha).round(2)
    small = gdf[gdf["area_ha"] < SMALL_POLYGON_HA]
    report.small_polygons = records(small, ["estado", KEY, "nombre", "area_ha"])
    return gdf


def check_nulls(gdf: gpd.GeoDataFrame, report: RanValidationReport) -> None:
    cols = [c for c in gdf.columns if c not in ("geom", "motivo_invalidez")]
    report.null_counts = {c: int(gdf[c].isna().sum()) for c in cols}


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------


def validate_ejidos(
    raw: gpd.GeoDataFrame, settings: Settings
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, RanValidationReport]:
    """Devuelve (ejidos limpios, traslapes entre ejidos, reporte)."""
    report = RanValidationReport()
    read_counts = raw.groupby("estado").size()
    gdf = ensure_crs_2d(raw.copy(), report)
    gdf = drop_null_geometries(gdf, report)
    gdf = fix_invalid(gdf, report)
    gdf = normalize_text(gdf, report)
    gdf = resolve_duplicate_keys(gdf, report)
    gdf = compute_areas(gdf, report)
    check_nulls(gdf, report)
    overlaps = find_overlaps(gdf, settings, report)

    for state in settings.states:
        sub = gdf[gdf["estado"] == state.code]
        report.per_state[state.code] = {
            "nombre": state.name,
            "leidos": int(read_counts.get(state.code, 0)),
            "salida": int(len(sub)),
            "area_ha": round(float(sub["area_ha"].sum()), 1),
            "ejidos": int((sub["tipo"] == "Ejido").sum()),
            "comunidades": int((sub["tipo"] == "Comunidad").sum()),
        }
    columns = ["estado", KEY, "nombre", "tipo", "municipio", "municipio_norm", "clv_unica", "folio_matriz",
               "edo_clv", "mun_clv", "nuc_clv", "programa", "programa_norm", "area_ha",
               "geom_reparada", "motivo_invalidez", "archivo_origen", "geom"]  # fmt: skip
    gdf = gdf[columns].sort_values(["estado", KEY]).reset_index(drop=True)
    return gdf, overlaps, report
