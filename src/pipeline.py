"""Orquestador del proyecto «Incendios y pérdida forestal en ejidos».

Etapas:
  1. ANP     ArcGIS FeatureServer -> validación               (capa de contexto)
  2. RAN     núcleos agrarios (KML) -> validación              (unidad de análisis)
  3. GEE     Hansen + MCD64A1 + FIRMS por ejido y año          (caché en data/gee/)
  4. PostGIS carga, cruce ejidos-ANP, vistas
  5. Salidas gráficas, reporte de calidad, GeoJSON del mapa

Uso:
    python -m src.pipeline                  # todo
    python -m src.pipeline --skip-gee       # solo caché de Earth Engine (sin cuenta de GEE)
    python -m src.pipeline --skip-db        # sin PostGIS (sin cruce con ANP)
    python -m src.pipeline --skip-extract   # reutiliza la descarga de ANP
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import geopandas as gpd


from .config import get_settings
from .extract.arcgis import extract_layer, load_extract_metadata
from .extract.ran import read_states
from .gee.run import cache_path, compute_metrics
from .load.postgis import load_analysis, load_to_postgis, read_results
from .process.export_web import export_ejidos, export_web
from .process.metrics import headline, summarize_ejidos, summarize_states
from .process.validate_anp import validate
from .process.validate_ran import validate_ejidos
from .report.charts import make_charts
from .report.quality import write_reports

log = logging.getLogger("pipeline")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incendios y pérdida forestal en ejidos (ArcGIS REST + RAN + GEE + PostGIS)")
    parser.add_argument("--skip-extract", action="store_true", help="usar el GeoJSON de ANP ya descargado")
    parser.add_argument("--skip-gee", action="store_true", help="no llamar a Earth Engine; usar solo data/gee/*.csv")
    parser.add_argument("--skip-db", action="store_true", help="no cargar a PostGIS (omite el cruce con ANP)")
    parser.add_argument("-v", "--verbose", action="store_true", help="más detalle en el log")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()
    state_names = {s.code: s.name for s in settings.states}
    log.info("Estados: %s | años %d-%d", ", ".join(state_names.values()), *settings.years)

    # 1. Capa de contexto -------------------------------------------------------
    log.info("1/5 ANP desde %s", settings.layer_url)
    extract = load_extract_metadata(settings) if args.skip_extract else extract_layer(settings)
    anp, anp_report = validate(gpd.read_file(settings.raw_geojson_path, engine="pyogrio"), settings)
    settings.processed_gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    anp.to_file(settings.processed_gpkg_path, layer=settings.db_table, driver="GPKG", engine="pyogrio")
    settings.validation_json_path.write_text(
        json.dumps(anp_report.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # 2. Ejidos -------------------------------------------------------------------
    log.info("2/5 Núcleos agrarios del RAN")
    ejidos, overlaps, ran_report = validate_ejidos(read_states(settings), settings)
    ejidos.to_file(settings.ejidos_gpkg_path, layer="nucleos_agrarios", driver="GPKG", engine="pyogrio")

    # 3. Earth Engine -------------------------------------------------------------
    metrics = summary = series = cifras = None
    missing = [s.name for s in settings.states if not cache_path(settings, s).exists()]
    if args.skip_gee and missing:
        log.warning("3/5 Sin caché de Earth Engine para %s: se omiten métricas (quita --skip-gee para calcularlas)", missing)
    else:
        log.info("3/5 Métricas de Earth Engine%s", " (desde caché)" if not missing else "")
        metrics = compute_metrics(settings, ejidos)
        summary = summarize_ejidos(metrics, ejidos)
        series = summarize_states(metrics, ejidos)
        cifras = headline(metrics, summary, series)

    # 4. PostGIS ------------------------------------------------------------------
    anp_load = ran_load = None
    comparison: list[dict] = []
    if args.skip_db:
        log.info("4/5 PostGIS omitido (--skip-db)")
        results = ejidos.merge(summary, on=[c for c in ("clave", "estado", "nombre", "municipio_norm", "tipo", "area_ha")]) \
            if summary is not None else None  # fmt: skip
    else:
        log.info("4/5 PostGIS (%s:%s/%s)", settings.db_host, settings.db_port, settings.db_name)
        anp_load = load_to_postgis(anp, settings)
        ran_load = load_analysis(settings, ejidos, overlaps, metrics, summary, series)
        comparison = ran_load["comparacion_anp"]
        results = read_results(settings) if metrics is not None else None

    # 5. Salidas ------------------------------------------------------------------
    log.info("5/5 Mapa, gráficas y reporte")
    web = export_web(anp, settings, extract, anp_report)
    if results is not None:
        export_ejidos(gpd.GeoDataFrame(results, geometry="geom"), metrics, series, cifras, _plain(comparison), settings)
        make_charts(summary, series, state_names, settings.docs_dir / "img")
    write_reports(
        settings, extract, anp_report, anp_load, web,
        extras={"ran_report": ran_report, "ran_load": ran_load, "headline": cifras, "comparison": comparison},
    )  # fmt: skip
    if cifras:
        for code, h in cifras.items():
            log.info("%s: %.1f %% de la pérdida forestal en ejidos coincide con área quemada (%s ha de %s ha)",
                     state_names[code], h["pct_perdida_fuego"], f"{h['perdida_fuego_ha']:,.0f}", f"{h['perdida_ha']:,.0f}")  # fmt: skip
    log.info("Listo. Reporte: reports/reporte_calidad.md | Mapa: web/index.html")
    return 0


def _plain(rows: list[dict]) -> list[dict]:
    """Decimal de PostgreSQL -> float, para serializar a JSON."""
    return [{k: float(v) if hasattr(v, "is_finite") else v for k, v in r.items()} for r in rows]


if __name__ == "__main__":
    sys.exit(main())
