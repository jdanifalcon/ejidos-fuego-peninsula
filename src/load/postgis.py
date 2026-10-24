"""Carga a PostgreSQL/PostGIS con GeoPandas + SQLAlchemy (GeoAlchemy2).

Esquemas:
- `conanp`   capa de contexto (ANP federales) extraída del FeatureServer.
- `ran`      núcleos agrarios validados y traslapes entre ejidos.
- `analisis` métricas de Earth Engine, resúmenes, cruce con ANP y vistas (sql/*.sql).

Decisiones:
- La carga *reemplaza* la tabla: el pipeline es idempotente (correrlo dos veces da
  el mismo resultado) y la fuente es la verdad.
- Geometría tipada `MULTIPOLYGON, 4326`: PostGIS rechaza cualquier otro tipo o SRID.
- Clave primaria natural (`id_anp`) e índice espacial GiST explícito.
- Después de cargar se ejecutan consultas de verificación cuyo resultado va al reporte.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from ..config import Settings
from ..process.validate_anp import ID_FIELD

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"

# Punto de referencia para la consulta espacial de ejemplo: Xpujil, Calakmul (Campeche).
SAMPLE_POINT = {"name": "Xpujil, Calakmul", "lon": -89.3944, "lat": 18.5096, "radius_km": 50}


def get_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def wait_for_db(engine: Engine, timeout_s: int = 60) -> None:
    """Espera a que PostGIS acepte conexiones (útil justo después de `docker compose up`)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "No se pudo conectar a PostGIS. ¿Está corriendo `docker compose up -d` y coincide el .env?"
                ) from exc
            log.info("Esperando a PostGIS...")
            time.sleep(2)


def _prepare_for_sql(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Columnas auxiliares que no deben ir a la base y tipos amigables para SQL."""
    return gdf.drop(columns=["fid"], errors="ignore").copy()


def load_to_postgis(gdf: gpd.GeoDataFrame, settings: Settings, engine: Engine | None = None) -> dict[str, Any]:
    """Carga la capa y devuelve un diccionario con las verificaciones posteriores."""
    engine = engine or get_engine(settings)
    wait_for_db(engine)
    schema, table = settings.db_schema, settings.db_table
    qualified = f"{schema}.{table}"
    index_name = f"idx_{table}_geom"

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    # GeoPandas tipa la columna como Geometry(MULTIPOLYGON, 4326) a partir de los datos
    # (por eso validate.py uniforma todo a MultiPolygon) y GeoAlchemy2 crea el índice
    # GiST `idx_<tabla>_geom`. El CREATE INDEX de abajo lo hace explícito e idempotente.
    _prepare_for_sql(gdf).to_postgis(table, engine, schema=schema, if_exists="replace", index=False)

    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {qualified} ADD PRIMARY KEY ({ID_FIELD})"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {qualified} USING GIST (geom)"))
        conn.execute(text(f"COMMENT ON TABLE {qualified} IS :c"), {"c": f"ANP federales CONANP. Fuente: {settings.layer_url}"})
        conn.execute(text(f"ANALYZE {qualified}"))

    result = verify_load(engine, settings)
    log.info("Cargadas %d filas en %s", result["rows"], qualified)
    return result


def verify_load(engine: Engine, settings: Settings) -> dict[str, Any]:
    """Consultas que comprueban que la carga quedó bien: conteo, SRID, validez e índice."""
    schema, table = settings.db_schema, settings.db_table
    qualified = f"{schema}.{table}"
    with engine.connect() as conn:
        summary = conn.execute(
            text(
                f"""
                SELECT count(*)                                         AS rows,
                       count(*) FILTER (WHERE NOT ST_IsValid(geom))     AS invalid,
                       count(DISTINCT ST_GeometryType(geom))            AS geom_types,
                       min(ST_SRID(geom))                               AS srid,
                       round(sum(ST_Area(geom::geography)) / 1e4)       AS total_ha
                FROM {qualified}
                """
            )
        ).mappings().one()
        indexes = conn.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = :t ORDER BY indexname"),
            {"s": schema, "t": table},
        ).mappings().all()
        # Ejemplo de consulta espacial: ANP a menos de N km de un punto de referencia.
        # `geography` hace que la distancia sea en metros sobre el elipsoide.
        nearby = conn.execute(
            text(
                f"""
                SELECT nombre, cat_manejo,
                       round((ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000)::numeric, 1) AS km
                FROM {qualified}
                WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)
                ORDER BY km
                """
            ),
            {"lon": SAMPLE_POINT["lon"], "lat": SAMPLE_POINT["lat"], "radius_m": SAMPLE_POINT["radius_km"] * 1000},
        ).mappings().all()
        postgis_version = conn.execute(text("SELECT postgis_lib_version()")).scalar_one()

    return {
        "table": qualified,
        "rows": int(summary["rows"]),
        "invalid": int(summary["invalid"]),
        "geometry_types": int(summary["geom_types"]),
        "srid": int(summary["srid"]),
        "total_ha": float(summary["total_ha"]),
        "indexes": [dict(i) for i in indexes],
        "postgis_version": postgis_version,
        "sample_query": {**SAMPLE_POINT, "results": [dict(r) | {"km": float(r["km"])} for r in nearby]},
    }


# ---------------------------------------------------------------------------
# Ejidos y análisis
# ---------------------------------------------------------------------------


def run_sql_file(engine: Engine, name: str, **params: str) -> None:
    """Ejecuta un archivo de sql/ completo en una transacción ({marcadores} sustituidos).

    Se envía tal cual al driver: psycopg2 acepta varias sentencias por llamada, y así
    no hay que partir el script por `;` (que también aparece dentro de cadenas).
    """
    script = (SQL_DIR / name).read_text(encoding="utf-8").format(**params)
    with engine.begin() as conn:
        # Cursor del driver sin parámetros: así psycopg2 no interpreta los '%' del script.
        with conn.connection.cursor() as cursor:
            cursor.execute(script)


def load_analysis(
    settings: Settings,
    ejidos: gpd.GeoDataFrame,
    overlaps: gpd.GeoDataFrame,
    metrics: pd.DataFrame | None,
    summary: pd.DataFrame | None,
    series: pd.DataFrame | None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Carga ejidos (y métricas si las hay), cruza con ANP y devuelve verificaciones."""
    engine = engine or get_engine(settings)
    wait_for_db(engine)
    with engine.begin() as conn:
        # Todo lo de estos esquemas se regenera en cada corrida: carga idempotente.
        conn.execute(text("DROP SCHEMA IF EXISTS analisis CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS ran CASCADE"))
        conn.execute(text("CREATE SCHEMA ran"))
        conn.execute(text("CREATE SCHEMA analisis"))

    ejidos.to_postgis("nucleos_agrarios", engine, schema="ran", index=False)
    overlaps.to_postgis("traslapes", engine, schema="ran", index=False)

    if metrics is not None:
        metrics.to_sql("metricas_ejido_anual", engine, schema="analisis", index=False)
        summary.to_sql("resumen_ejido", engine, schema="analisis", index=False)
        series.to_sql("metricas_estado_anual", engine, schema="analisis", index=False)
        run_sql_file(engine, "10_indices_ran.sql")
    else:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE ran.nucleos_agrarios ADD PRIMARY KEY (clave)"))

    anp = f"{settings.db_schema}.{settings.db_table}"
    run_sql_file(engine, "20_cruce_anp.sql", anp=anp)
    if metrics is not None:
        run_sql_file(engine, "30_vistas.sql")

    with engine.connect() as conn:
        counts = conn.execute(text(
            "SELECT (SELECT count(*) FROM ran.nucleos_agrarios) AS ejidos, "
            "(SELECT count(*) FROM ran.nucleos_agrarios WHERE NOT ST_IsValid(geom)) AS invalidas, "
            "(SELECT count(*) FROM ran.traslapes) AS traslapes, "
            "(SELECT count(DISTINCT clave) FROM analisis.ejido_anp) AS ejidos_con_anp"
        )).mappings().one()
        relation = conn.execute(text(
            "SELECT e.estado, coalesce(x.relacion, 'fuera') AS relacion, count(*) AS ejidos "
            "FROM ran.nucleos_agrarios e LEFT JOIN ("
            "  SELECT clave, CASE WHEN bool_or(relacion = 'dentro') THEN 'dentro' ELSE 'intersecta' END AS relacion"
            "  FROM analisis.ejido_anp GROUP BY clave) x USING (clave) "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )).mappings().all()
        comparison = []
        if metrics is not None:
            comparison = [dict(r) for r in conn.execute(text("SELECT * FROM analisis.v_comparacion_anp")).mappings()]
    result = {**dict(counts), "relacion_anp": [dict(r) for r in relation], "comparacion_anp": comparison}
    log.info("PostGIS: %s ejidos, %s con intersección con ANP", counts["ejidos"], counts["ejidos_con_anp"])
    return result


def read_results(settings: Settings, engine: Engine | None = None) -> gpd.GeoDataFrame:
    """Resumen por ejido con relación a ANP (vista `analisis.v_resumen_ejido`)."""
    engine = engine or get_engine(settings)
    return gpd.read_postgis("SELECT * FROM analisis.v_resumen_ejido", engine, geom_col="geom")
