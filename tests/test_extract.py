"""Pruebas de la paginación sin red: una sesión falsa simula al FeatureServer."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.config import get_settings
from src.extract.arcgis import ArcGISError, extract_layer, iter_pages

LAYER = "https://example.com/arcgis/rest/services/Demo/FeatureServer/0"
TOTAL = 23


def _feature(oid: int) -> dict:
    return {
        "type": "Feature",
        "id": oid,
        "properties": {"FID": oid, "NOMBRE": f"ANP {oid}"},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    }


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Responde como un FeatureServer con TOTAL entidades y registra cada consulta."""

    def __init__(self, supports_pagination: bool = True, flag_exceeded: bool = True):
        self.supports_pagination = supports_pagination
        self.flag_exceeded = flag_exceeded
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        if url == LAYER:
            return FakeResponse({
                "name": "Demo", "geometryType": "esriGeometryPolygon", "objectIdField": "FID",
                "fields": [{"name": "FID", "type": "esriFieldTypeOID"}], "maxRecordCount": 10,
                "supportedQueryFormats": "JSON, geoJSON",
                "advancedQueryCapabilities": {"supportsPagination": self.supports_pagination},
            })  # fmt: skip
        if params.get("returnCountOnly"):
            return FakeResponse({"count": TOTAL})
        if params.get("returnIdsOnly"):
            return FakeResponse({"objectIds": list(range(1, TOTAL + 1))})
        offset, size = params["resultOffset"], params["resultRecordCount"]
        oids = list(range(1, TOTAL + 1))[offset : offset + size]
        exceeded = self.flag_exceeded and offset + size < TOTAL
        return FakeResponse({
            "type": "FeatureCollection", "features": [_feature(i) for i in oids],
            "properties": {"exceededTransferLimit": exceeded},
        })  # fmt: skip

    def post(self, url, data=None, timeout=None):
        ids = [int(i) for i in data["objectIds"].split(",")]
        return FakeResponse({"type": "FeatureCollection", "features": [_feature(i) for i in ids]})


@pytest.fixture
def settings(tmp_path):
    base = get_settings()
    return replace(base, layer_url=LAYER, page_size=50, data_dir=tmp_path / "data", dataset_name="demo")


def test_page_size_is_capped_by_max_record_count(settings):
    session = FakeSession()
    result = extract_layer(settings, session=session)
    assert result.page_size == 10  # pedimos 50, el servidor permite 10
    assert result.pages == 3
    assert result.received_count == result.expected_count == TOTAL


def test_pages_are_ordered_by_object_id(settings):
    session = FakeSession()
    extract_layer(settings, session=session)
    page_calls = [c for c in session.calls if "resultOffset" in c]
    assert [c["resultOffset"] for c in page_calls] == [0, 10, 20]
    assert all(c["orderByFields"] == "FID ASC" for c in page_calls)
    assert all(c["outSR"] == 4326 and c["f"] == "geojson" for c in page_calls)


def test_output_file_is_valid_geojson(settings):
    result = extract_layer(settings, session=FakeSession())
    data = json.loads((settings.data_dir / "raw" / "demo.geojson").read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == TOTAL
    assert result.method == "resultOffset/resultRecordCount"


def test_keeps_paging_when_server_omits_exceeded_flag():
    pages = list(iter_pages(FakeSession(flag_exceeded=False), LAYER, where="1=1", order_by="FID", page_size=10, timeout=5))
    assert [len(p) for p in pages] == [10, 10, 3]


def test_falls_back_to_object_ids_without_pagination(settings):
    result = extract_layer(settings, session=FakeSession(supports_pagination=False))
    assert result.method == "objectIds"
    assert result.received_count == TOTAL


def test_arcgis_error_payload_raises():
    class ErrorSession(FakeSession):
        def get(self, url, params=None, timeout=None):
            return FakeResponse({"error": {"code": 499, "message": "Token Required"}})

    with pytest.raises(ArcGISError, match="499"):
        list(iter_pages(ErrorSession(), LAYER, where="1=1", order_by="FID", page_size=10, timeout=5))
