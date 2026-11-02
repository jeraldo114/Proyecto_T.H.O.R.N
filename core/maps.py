"""Lugares con coordenadas documentadas; no lee GPS ni consulta en segundo plano."""

from __future__ import annotations

import json
import math
from urllib.parse import urlencode

from .internet import InternetService, clean
from .public_web import public_url


async def find_places(internet: InternetService, query: object, *, consent: bool,
                      request_id: str) -> dict[str, object]:
    """Usa articulos de Wikipedia como fuente y enlaces OSM como destinos."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
        raise ValueError("map_invalid_query")
    async with internet.operation("map", consent, request_id):
        params = {"action": "query", "format": "json", "formatversion": 2,
                  "generator": "search", "gsrsearch": query.strip(), "gsrlimit": 3,
                  "prop": "coordinates|info", "inprop": "url", "colimit": 1}
        raw, _, _ = await internet.client.get("https://es.wikipedia.org/w/api.php?" + urlencode(params), json_only=True)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("web_invalid_response")
        pages = value.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            raise RuntimeError("web_invalid_response")
        places = []
        for page in sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("index", 999))[:3]:
            coordinates = page.get("coordinates", [])
            if not isinstance(coordinates, list) or not coordinates or not isinstance(coordinates[0], dict):
                continue
            lat, lon = coordinates[0].get("lat"), coordinates[0].get("lon")
            if (type(lat) not in {int, float} or type(lon) not in {int, float} or
                    not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                continue
            source = public_url(page.get("fullurl"))
            places.append({"title": clean(page.get("title"), 140), "latitude": lat, "longitude": lon,
                           "source_url": source,
                           "map_url": f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=12/{lat:.6f}/{lon:.6f}"})
        return {"places": places, "provider": "Wikipedia / OpenStreetMap",
                "attribution": "Coordenadas: Wikipedia; mapa: © OpenStreetMap contributors",
                "precision": "Coordenadas del articulo, no geocodificacion de direcciones ni posicion GPS",
                "request_id": request_id}
