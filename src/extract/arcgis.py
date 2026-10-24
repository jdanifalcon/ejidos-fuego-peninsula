"""Extracción de una capa de un ArcGIS FeatureServer público (sin token).

Flujo:
1. Leer los metadatos de la capa (`?f=json`): campo OID, `maxRecordCount`,
   si soporta paginación y formatos de consulta.
2. Pedir el total esperado con `returnCountOnly=true`.
3. Descargar por páginas con `resultOffset` / `resultRecordCount` en formato
   GeoJSON, ordenando por el OID para que las páginas sean estables.
4. Comprobar que lo recibido coincide con lo esperado y guardar el GeoJSON crudo
   más un archivo de metadatos (procedencia).

Si la capa no soporta paginación, se usa el método alterno de ArcGIS:
`returnIdsOnly=true` y luego pedir los registros en lotes de `objectIds`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Settings

log = logging.getLogger(__name__)

USER_AGENT = "arcgis-rest-postgis-leaflet/1.0 (proyecto de portafolio)"
MAX_PAGES = 10_000  # tope de seguridad contra ciclos infinitos


class ArcGISError(RuntimeError):
    """Error devuelto por el servicio. ArcGIS responde HTTP 200 con un objeto {"error": ...}."""


@dataclass
class ExtractResult:
    path: str
    layer_name: str
    source_url: str
    where: str
    expected_count: int
    received_count: int
    pages: int
    page_size: int
    max_record_count: int | None
    method: str
    fetched_at: str


def build_session(retries: int = 3, backoff: float = 1.0) -> requests.Session:
    """Sesión HTTP con reintentos exponenciales para errores transitorios (429/5xx)."""
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = USER_AGENT
    return session


def _request_json(
    session: requests.Session, url: str, params: dict[str, Any], timeout: int, method: str = "GET"
) -> dict[str, Any]:
    """GET (o POST) que valida HTTP *y* el objeto `error` propio de ArcGIS."""
    if method == "POST":
        response = session.post(url, data=params, timeout=timeout)
    else:
        response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        err = data["error"]
        raise ArcGISError(f"ArcGIS {err.get('code')}: {err.get('message')} {err.get('details') or ''}".strip())
    return data


def get_layer_metadata(session: requests.Session, layer_url: str, timeout: int) -> dict[str, Any]:
    """Metadatos de la capa; falla pronto si la URL no apunta a una capa de entidades."""
    meta = _request_json(session, layer_url, {"f": "json"}, timeout)
    if "geometryType" not in meta or "fields" not in meta:
        raise ArcGISError(f"{layer_url} no parece una capa de un FeatureServer (¿falta el /<id> de la capa?)")
    if "geoJSON" not in (meta.get("supportedQueryFormats") or ""):
        raise ArcGISError("La capa no ofrece salida GeoJSON (supportedQueryFormats)")
    return meta


def object_id_field(meta: dict[str, Any]) -> str:
    """Nombre del campo OID; se usa para ordenar y así garantizar páginas estables."""
    if meta.get("objectIdField"):
        return meta["objectIdField"]
    for field in meta["fields"]:
        if field["type"] == "esriFieldTypeOID":
            return field["name"]
    raise ArcGISError("La capa no tiene campo OID")


def get_feature_count(session: requests.Session, layer_url: str, where: str, timeout: int) -> int:
    data = _request_json(session, f"{layer_url}/query", {"where": where, "returnCountOnly": "true", "f": "json"}, timeout)
    return int(data["count"])


def _base_query_params(where: str, order_by: str) -> dict[str, Any]:
    return {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,  # RFC 7946: GeoJSON siempre en WGS84
        "orderByFields": f"{order_by} ASC",
        "f": "geojson",
    }


def iter_pages(
    session: requests.Session,
    layer_url: str,
    *,
    where: str,
    order_by: str,
    page_size: int,
    timeout: int,
) -> Iterator[list[dict[str, Any]]]:
    """Genera páginas de entidades usando resultOffset / resultRecordCount.

    Condición de paro: página vacía, o página incompleta sin `exceededTransferLimit`.
    Se revisan las dos señales porque algunos servidores no marcan
    `exceededTransferLimit` cuando se pide `resultRecordCount` explícito.
    """
    offset = 0
    for _ in range(MAX_PAGES):
        params = _base_query_params(where, order_by) | {"resultOffset": offset, "resultRecordCount": page_size}
        data = _request_json(session, f"{layer_url}/query", params, timeout)
        features = data.get("features", [])
        # En GeoJSON la bandera viene en `properties`; en JSON de Esri, en la raíz.
        exceeded = bool(data.get("properties", {}).get("exceededTransferLimit") or data.get("exceededTransferLimit"))
        log.info("Página offset=%d: %d entidades (exceededTransferLimit=%s)", offset, len(features), exceeded)
        if not features:
            return
        yield features
        if not exceeded and len(features) < page_size:
            return
        offset += len(features)
    raise ArcGISError(f"Se alcanzó el tope de {MAX_PAGES} páginas; revisa el filtro o el tamaño de página")


def iter_pages_by_object_ids(
    session: requests.Session,
    layer_url: str,
    *,
    where: str,
    order_by: str,
    page_size: int,
    timeout: int,
) -> Iterator[list[dict[str, Any]]]:
    """Alternativa para servicios sin `supportsPagination`: lotes de objectIds."""
    data = _request_json(session, f"{layer_url}/query", {"where": where, "returnIdsOnly": "true", "f": "json"}, timeout)
    ids = sorted(data.get("objectIds") or [])
    for start in range(0, len(ids), page_size):
        batch = ids[start : start + page_size]
        params = _base_query_params("1=1", order_by) | {"objectIds": ",".join(map(str, batch))}
        # POST evita URLs demasiado largas con muchos IDs.
        page = _request_json(session, f"{layer_url}/query", params, timeout, method="POST")
        log.info("Lote objectIds %d-%d: %d entidades", start, start + len(batch), len(page.get("features", [])))
        yield page.get("features", [])


def extract_layer(settings: Settings, session: requests.Session | None = None) -> ExtractResult:
    """Descarga la capa completa (según `settings.where`) y la guarda como GeoJSON crudo."""
    session = session or build_session()
    meta = get_layer_metadata(session, settings.layer_url, settings.request_timeout)
    oid = object_id_field(meta)
    max_records = meta.get("maxRecordCount")
    # Nunca pedir más de lo que el servidor permite por consulta.
    page_size = min(settings.page_size, max_records) if max_records else settings.page_size
    supports_pagination = bool(meta.get("advancedQueryCapabilities", {}).get("supportsPagination"))

    expected = get_feature_count(session, settings.layer_url, settings.where, settings.request_timeout)
    log.info("Capa '%s': %d entidades esperadas; maxRecordCount=%s; página=%d", meta.get("name"), expected, max_records, page_size)

    pager = iter_pages if supports_pagination else iter_pages_by_object_ids
    features: list[dict[str, Any]] = []
    pages = 0
    for page in pager(
        session, settings.layer_url, where=settings.where, order_by=oid, page_size=page_size, timeout=settings.request_timeout
    ):
        features.extend(page)
        pages += 1

    # Verificación: mismo total y sin OIDs repetidos entre páginas.
    oids = [f.get("id", f.get("properties", {}).get(oid)) for f in features]
    if len(set(oids)) != len(oids):
        raise ArcGISError("Se recibieron entidades repetidas entre páginas; la paginación no es estable")
    if len(features) != expected:
        log.warning("Se esperaban %d entidades y se recibieron %d", expected, len(features))

    settings.raw_geojson_path.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    settings.raw_geojson_path.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")

    result = ExtractResult(
        path=str(settings.raw_geojson_path.relative_to(settings.data_dir.parent).as_posix()),
        layer_name=meta.get("name", ""),
        source_url=settings.layer_url,
        where=settings.where,
        expected_count=expected,
        received_count=len(features),
        pages=pages,
        page_size=page_size,
        max_record_count=max_records,
        method="resultOffset/resultRecordCount" if supports_pagination else "objectIds",
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    settings.raw_metadata_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Guardado %s (%d entidades en %d páginas)", result.path, result.received_count, pages)
    return result


def load_extract_metadata(settings: Settings) -> ExtractResult:
    """Lee los metadatos de una extracción previa (para `--skip-extract`)."""
    data = json.loads(settings.raw_metadata_path.read_text(encoding="utf-8"))
    return ExtractResult(**data)
