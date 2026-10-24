"""Ejecución de las métricas de Earth Engine por ejido, con lotes y caché en CSV.

- Los ejidos se envían a Earth Engine simplificados a 5 m (muy por debajo del pixel de
  30 m) y en lotes de GEE_BATCH_SIZE; si un lote falla por tiempo o memoria se parte
  a la mitad y se reintenta.
- El resultado de cada estado se guarda en `data/gee/*.csv` y se versiona en Git:
  cualquiera puede reproducir PostGIS, gráficas y mapa sin cuenta de Earth Engine,
  y el CI no necesita credenciales.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from ..config import Settings, State
from ..process.geometry import MEXICO_LCC, WGS84
from .auth import init_ee
from .forest_fire import FULL_METRICS, HANSEN_CRS, HANSEN_TRANSFORM, firms_image, metrics_image

log = logging.getLogger(__name__)

GEE_SIMPLIFY_M = 5
TILE_SCALE = 4  # más memoria por tile a costa de velocidad: evita "User memory limit exceeded"


def cache_path(settings: Settings, state: State) -> Path:
    first, last = settings.years
    name = (f"metricas_{state.code}_{settings.hansen_version}_t{settings.treecover_threshold}"
            f"_sens{settings.fire_buffer_sens_m}_{first}-{last}.csv")  # fmt: skip
    return settings.gee_dir / name


def _to_feature_collection(ee: ModuleType, ejidos: gpd.GeoDataFrame) -> Any:
    simplified = ejidos.to_crs(MEXICO_LCC).simplify(GEE_SIMPLIFY_M, preserve_topology=True).to_crs(WGS84)
    features = [
        ee.Feature(ee.Geometry(mapping(geom), opt_proj="EPSG:4326", opt_geodesic=False), {"clave": clave})
        for clave, geom in zip(ejidos["clave"], simplified)
    ]
    return ee.FeatureCollection(features)


def _reduce(ee: ModuleType, image: Any, fc: Any, *, firms: bool = False) -> list[dict[str, Any]]:
    if firms:
        # FIRMS: conteo de pixeles de 1 km (centro dentro del ejido) × días con foco.
        result = image.reduceRegions(collection=fc, reducer=ee.Reducer.sum().unweighted(), scale=1000, tileScale=TILE_SCALE)
    else:
        result = image.reduceRegions(
            collection=fc, reducer=ee.Reducer.sum(), crs=HANSEN_CRS, crsTransform=HANSEN_TRANSFORM, tileScale=TILE_SCALE
        )
    return [f["properties"] for f in result.getInfo()["features"]]


def _reduce_batch(ee: ModuleType, images: dict[str, tuple[Any, bool]], batch: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    fc = _to_feature_collection(ee, batch)
    merged: dict[str, dict[str, Any]] = {c: {"clave": c} for c in batch["clave"]}
    for image, firms in images.values():
        for props in _reduce(ee, image, fc, firms=firms):
            merged[props["clave"]].update(props)
    return list(merged.values())


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc)
    return "Too Many Requests" in text or "concurrency limit" in text or "429" in text


def _run_batches(
    ee: ModuleType,
    images: dict[str, tuple[Any, bool]],
    ejidos: gpd.GeoDataFrame,
    batch_size: int,
    workers: int,
    on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
) -> pd.DataFrame:
    """Aplica las imágenes a los ejidos en lotes concurrentes (la API interactiva admite
    varias solicitudes a la vez, hasta el límite del proyecto). Un lote rechazado por
    concurrencia (429) se reintenta igual tras una pausa; uno que falla por tiempo o
    memoria se parte a la mitad. `on_batch` recibe cada lote terminado (guardado parcial)."""
    rows: list[dict[str, Any]] = []
    pending = [ejidos.iloc[i : i + batch_size] for i in range(0, len(ejidos), batch_size)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running = {pool.submit(_reduce_batch, ee, images, b): b for b in pending}
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                batch = running.pop(future)
                try:
                    result = future.result()
                except ee.EEException as exc:
                    if _is_rate_limit(exc):
                        # Límite de solicitudes simultáneas del proyecto: reintentar el mismo lote
                        # tras una pausa (partirlo solo generaría más solicitudes).
                        log.info("Límite de concurrencia de Earth Engine; se reintenta un lote de %d", len(batch))
                        time.sleep(10)
                        running[pool.submit(_reduce_batch, ee, images, batch)] = batch
                        continue
                    if len(batch) == 1:
                        raise RuntimeError(f"Earth Engine falló con el ejido {batch['clave'].iat[0]}: {exc}") from exc
                    half = len(batch) // 2
                    log.warning("Lote de %d falló (%s); se reintenta en dos lotes", len(batch), str(exc)[:80])
                    for part in (batch.iloc[:half], batch.iloc[half:]):
                        running[pool.submit(_reduce_batch, ee, images, part)] = part
                    continue
                rows.extend(result)
                if on_batch:
                    on_batch(result)
                log.info("Earth Engine: %d/%d ejidos", len(rows), len(ejidos))
    return pd.DataFrame(rows)


def compute_state(settings: Settings, state: State, ejidos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Calcula (o lee de caché) la tabla ancha de métricas de un estado."""
    path = cache_path(settings, state)
    if path.exists():
        log.info("Earth Engine %s: usando caché %s", state.name, path.name)
        return pd.read_csv(path, dtype={"clave": str})

    ee = init_ee(settings.ee_project)
    kwargs = {"hansen_asset": settings.hansen_asset, "years": settings.years, "threshold": settings.treecover_threshold}
    images = {
        "principal": (metrics_image(ee, **kwargs), False),
        "firms": (firms_image(ee, years=settings.years, min_confidence=settings.firms_min_confidence), True),
    }
    if settings.fire_buffer_sens_m > 0:
        sens = metrics_image(ee, **kwargs, buffer_m=settings.fire_buffer_sens_m, only_fire_loss=True)
        # Renombra perdida_fuego_ha_<año> -> perdida_fuego_buffer_ha_<año>
        names = sens.bandNames().map(lambda b: ee.String(b).replace("perdida_fuego_ha", "perdida_fuego_buffer_ha"))
        images["sensibilidad"] = (sens.rename(names), False)

    # Guardado parcial: si la corrida se interrumpe, al reanudar se saltan los ejidos ya calculados.
    settings.gee_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".parcial.csv")
    done = pd.read_csv(partial, dtype={"clave": str}) if partial.exists() else pd.DataFrame(columns=["clave"])
    subset = ejidos[(ejidos["estado"] == state.code) & ~ejidos["clave"].isin(done["clave"])]
    subset = subset.sort_values("area_ha", ascending=False)
    log.info("Earth Engine %s: %d ejidos por calcular (%d ya en guardado parcial), %d-%d",
             state.name, len(subset), len(done), *settings.years)  # fmt: skip

    def save_partial(batch_rows: list[dict[str, Any]]) -> None:
        pd.DataFrame(batch_rows).to_csv(partial, mode="a", header=not partial.exists(), index=False, float_format="%.4f")

    new = _run_batches(ee, images, subset, settings.gee_batch_size, settings.gee_workers, on_batch=save_partial)
    wide = pd.concat([done, new], ignore_index=True) if len(done) else new
    wide.sort_values("clave").to_csv(path, index=False, float_format="%.4f")
    partial.unlink(missing_ok=True)
    meta = {
        "estado": state.name, "hansen": settings.hansen_asset, "mcd64a1": "MODIS/061/MCD64A1", "firms": "FIRMS",
        "anios": list(settings.years), "umbral_cobertura": settings.treecover_threshold,
        "buffer_sensibilidad_m": settings.fire_buffer_sens_m, "firms_confianza_min": settings.firms_min_confidence,
        "simplificacion_m": GEE_SIMPLIFY_M, "proyecto_ee": settings.ee_project,
        "calculado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }  # fmt: skip
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Earth Engine %s: guardado %s", state.name, path.name)
    return wide


def wide_to_long(wide: pd.DataFrame, years: tuple[int, int]) -> pd.DataFrame:
    """Tabla ancha (una columna por métrica y año) -> larga (clave, anio, métricas)."""
    frames = []
    for year in range(years[0], years[1] + 1):
        cols = {f"{m}_{year}": m for m in (*FULL_METRICS, "perdida_fuego_buffer_ha", "focos_firms")}
        present = {k: v for k, v in cols.items() if k in wide.columns}
        frame = wide[["clave", "bosque2000_ha", *present]].rename(columns=present)
        frames.append(frame.assign(anio=year))
    long = pd.concat(frames, ignore_index=True)
    if "focos_firms" in long:
        long["focos_firms"] = long["focos_firms"].round().astype("Int64")
    return long


def compute_metrics(settings: Settings, ejidos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Métricas anuales por ejido (formato largo) para todos los estados configurados."""
    long = pd.concat(
        [wide_to_long(compute_state(settings, s, ejidos), settings.years) for s in settings.states], ignore_index=True
    )
    missing = set(ejidos["clave"]) - set(long["clave"])
    if missing:
        raise RuntimeError(
            f"{len(missing)} ejidos no tienen métricas en la caché (¿cambió el RAN?). "
            "Borra el CSV de data/gee/ del estado y vuelve a ejecutar la etapa gee."
        )
    long["umbral_cobertura"] = settings.treecover_threshold
    long["version_hansen"] = settings.hansen_version
    return long
