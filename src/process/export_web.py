"""Exporta una versión ligera de la capa para el mapa Leaflet (GitHub Pages).

El GeoJSON completo pesa ~18 MB: demasiado para cargarlo en un navegador.
Se reduce así:
1. Simplificación en EPSG:6372 (tolerancia en *metros*, no en grados) con
   `preserve_topology=True` para no generar polígonos inválidos.
2. Coordenadas a 5 decimales (~1.1 m), suficiente para un mapa web.
3. Solo los atributos que muestra el mapa.

El archivo resultante se versiona en `web/data/`, así el mapa funciona en
GitHub Pages sin necesitar la base de datos.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import geopandas as gpd
import pandas as pd

from ..config import Settings
from ..extract.arcgis import ExtractResult
from .validate_anp import MEXICO_LCC, WGS84, ValidationReport

log = logging.getLogger(__name__)

WEB_COLUMNS = [
    "id_anp", "nombre", "cat_manejo", "categoria", "estados", "estados_norm", "en_peninsula",
    "superficie", "area_ha_geodesica", "dif_superficie_pct", "prim_dec", "tiene_programa_manejo",
    "geom_reparada", "motivo_invalidez",
]  # fmt: skip


def export_web(
    gdf: gpd.GeoDataFrame, settings: Settings, extract: ExtractResult, report: ValidationReport
) -> dict[str, Any]:
    out_dir = settings.web_data_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = out_dir / "anp.geojson"

    web = gdf[WEB_COLUMNS + ["geom"]].copy()
    web["prim_dec"] = web["prim_dec"].astype(str)
    web = web.to_crs(MEXICO_LCC)
    web["geom"] = web.geometry.simplify(settings.simplify_tolerance_m, preserve_topology=True)
    web = web.to_crs(WGS84)

    if geojson_path.exists():
        geojson_path.unlink()  # el driver GeoJSON no sobrescribe
    web.to_file(geojson_path, driver="GeoJSON", engine="pyogrio", RFC7946="YES", COORDINATE_PRECISION=5)

    size_mb = geojson_path.stat().st_size / 1_048_576
    metadata = {
        "fuente": "CONANP vía NOAA NCEI (Gulf of Mexico Data Atlas)",
        "url_servicio": extract.source_url,
        "capa": extract.layer_name,
        "descargado": extract.fetched_at,
        "total": int(len(web)),
        "en_peninsula": int(report.peninsula_count),
        "geometrias_reparadas": len(report.invalid_geometries),
        "tolerancia_simplificacion_m": settings.simplify_tolerance_m,
        "tamano_mb": round(size_mb, 2),
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Mapa: %s (%.2f MB, tolerancia %s m)", geojson_path.name, size_mb, settings.simplify_tolerance_m)
    return metadata


# ---------------------------------------------------------------------------
# Ejidos (análisis de pérdida forestal y fuego)
# ---------------------------------------------------------------------------

EJIDO_WEB_COLUMNS = [
    "clave", "estado", "nombre", "municipio_norm", "area_ha", "bosque2000_ha", "perdida_ha", "quemada_ha",
    "perdida_fuego_ha", "perdida_fuego_buffer_ha", "focos_firms", "pct_perdida_fuego", "pct_fuego_area",
    "pct_perdida_area", "rank_fuego_ha", "anio_max_fuego",
]  # fmt: skip
ANP_WEB_COLUMNS = ["relacion_anp", "pct_en_anp", "anp_nombres", "distancia_anp_km", "anp_cercana"]
EJIDO_SIMPLIFY_M = 40


def _series_by_ejido(long: pd.DataFrame, years: tuple[int, int]) -> dict[str, dict[str, list[float]]]:
    """{clave: {"p": [...], "f": [...], "q": [...]}} con un valor por año (para el popup)."""
    wide = long.pivot_table(index="clave", columns="anio", values=["perdida_ha", "perdida_fuego_ha", "quemada_ha"])
    year_list = list(range(years[0], years[1] + 1))
    out = {}
    for clave, row in wide.iterrows():
        out[clave] = {k: [round(float(row[(col, y)]), 1) for y in year_list]
                      for k, col in (("p", "perdida_ha"), ("f", "perdida_fuego_ha"), ("q", "quemada_ha"))}  # fmt: skip
    return out


def export_ejidos(
    results: gpd.GeoDataFrame,
    long: pd.DataFrame,
    series: pd.DataFrame,
    headline: dict[str, Any],
    comparison: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    """GeoJSON de ejidos con métricas y serie anual + resultados.json para el panel del mapa."""
    out_dir = settings.web_data_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ejidos.geojson"

    cols = EJIDO_WEB_COLUMNS + [c for c in ANP_WEB_COLUMNS if c in results.columns]
    web = results[cols + ["geom"]].copy()
    web = web.to_crs(MEXICO_LCC)
    web["geom"] = web.geometry.simplify(EJIDO_SIMPLIFY_M, preserve_topology=True)
    web = web.to_crs(WGS84)
    series_map = _series_by_ejido(long, settings.years)
    web["serie"] = web["clave"].map(lambda c: json.dumps(series_map.get(c, {}), separators=(",", ":")))
    if path.exists():
        path.unlink()
    web.to_file(path, driver="GeoJSON", engine="pyogrio", RFC7946="YES", COORDINATE_PRECISION=5)

    states = {s.code: s.name for s in settings.states}
    payload = {
        "anios": list(range(settings.years[0], settings.years[1] + 1)),
        "estados": states,
        "cifras": headline,
        "serie_estatal": {code: g.sort_values("anio").drop(columns="estado").to_dict(orient="list")
                          for code, g in series.groupby("estado")},  # fmt: skip
        "comparacion_anp": comparison,
        "parametros": {
            "hansen": settings.hansen_asset, "umbral_cobertura": settings.treecover_threshold,
            "buffer_sensibilidad_m": settings.fire_buffer_sens_m, "firms_confianza_min": settings.firms_min_confidence,
        },
    }  # fmt: skip
    (out_dir / "resultados.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    size_mb = path.stat().st_size / 1_048_576
    log.info("Mapa: ejidos.geojson (%.2f MB, %d ejidos)", size_mb, len(web))
    return {"ejidos": int(len(web)), "tamano_mb": round(size_mb, 2), "simplificacion_m": EJIDO_SIMPLIFY_M}
