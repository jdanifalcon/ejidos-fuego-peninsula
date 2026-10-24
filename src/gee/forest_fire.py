"""Construcción de las imágenes de Earth Engine: pérdida forestal, área quemada y su cruce.

Método (por año *a*):
- Bosque base: Hansen `treecover2000 >= UMBRAL` en tierra firme (`datamask == 1`).
- Pérdida en *a*: `lossyear == a - 2000` dentro del bosque base.
- Área quemada en *a*: algún mes de MODIS MCD64A1 con `BurnDate > 0`.
- Pérdida asociada a fuego en *a*: pérdida de *a* dentro del área quemada de *a* o de *a - 1*
  (un incendio de fin de año suele detectarse como pérdida al año siguiente).
- Sensibilidad: el área quemada se dilata `buffer_m` metros (MODIS omite incendios pequeños
  y sus bordes son imprecisos a 500 m).

Todas las superficies se miden con `ee.Image.pixelArea()` sobre la malla de Hansen
(~30 m, EPSG:4326 a 0.00025°). MODIS (500 m) y FIRMS (1 km) se remuestrean por
vecino más cercano a esa malla, que es el comportamiento por defecto de Earth Engine.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

MCD64A1 = "MODIS/061/MCD64A1"
FIRMS = "FIRMS"

# Malla nativa de Hansen GFC: así cada pixel de 30 m se cuenta exactamente una vez.
HANSEN_CRS = "EPSG:4326"
HANSEN_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 80]

# Nombres de las métricas anuales (bandas "<métrica>_<año>").
FULL_METRICS = ("perdida_ha", "quemada_ha", "quemada_bosque_ha", "perdida_fuego_ha")


def burned_mask(ee: ModuleType, year: int, buffer_m: int = 0) -> Any:
    """Máscara 0/1 de área quemada en el año, según MCD64A1."""
    collection = ee.ImageCollection(MCD64A1).filterDate(f"{year}-01-01", f"{year + 1}-01-01").select("BurnDate")
    burned = collection.max().gt(0).unmask(0)
    if buffer_m > 0:
        # La dilatación se hace en la malla de MODIS (barata) y luego se remuestrea a 30 m.
        modis = ee.Image(collection.first()).projection()
        burned = burned.reproject(modis).focalMax(radius=buffer_m, kernelType="circle", units="meters")
    return burned


def metrics_image(
    ee: ModuleType,
    *,
    hansen_asset: str,
    years: tuple[int, int],
    threshold: int,
    buffer_m: int = 0,
    only_fire_loss: bool = False,
) -> Any:
    """Imagen multibanda en hectáreas: bosque2000_ha + <métrica>_<año> para cada año.

    `only_fire_loss=True` calcula solo `perdida_fuego_ha_<año>` (para la corrida de sensibilidad).
    """
    gfc = ee.Image(hansen_asset)
    forest = gfc.select("treecover2000").gte(threshold).And(gfc.select("datamask").eq(1))
    lossyear = gfc.select("lossyear")
    area_ha = ee.Image.pixelArea().divide(10_000)

    first, last = years
    burned = {y: burned_mask(ee, y, buffer_m) for y in range(first - 1, last + 1)}

    bands = [] if only_fire_loss else [forest.multiply(area_ha).rename("bosque2000_ha")]
    for year in range(first, last + 1):
        loss = lossyear.eq(year - 2000).And(forest)
        fire_window = burned[year].Or(burned[year - 1])
        fire_loss = loss.And(fire_window).multiply(area_ha).rename(f"perdida_fuego_ha_{year}")
        if only_fire_loss:
            bands.append(fire_loss)
            continue
        bands += [
            loss.multiply(area_ha).rename(f"perdida_ha_{year}"),
            burned[year].multiply(area_ha).rename(f"quemada_ha_{year}"),
            burned[year].And(forest).multiply(area_ha).rename(f"quemada_bosque_ha_{year}"),
            fire_loss,
        ]
    return ee.Image.cat(bands).toFloat()


def firms_image(ee: ModuleType, *, years: tuple[int, int], min_confidence: int) -> Any:
    """Días con foco de calor por pixel de 1 km y por año (FIRMS, MODIS)."""
    bands = []
    for year in range(years[0], years[1] + 1):
        daily = (
            ee.ImageCollection(FIRMS)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .select("confidence")
            .map(lambda img: img.gte(min_confidence).selfMask())
        )
        bands.append(daily.count().unmask(0).rename(f"focos_firms_{year}"))
    return ee.Image.cat(bands)
