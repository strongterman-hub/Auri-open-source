from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.chinese import normalize_value

logger = logging.getLogger("auri.place")
RESOLVER_VERSION = 2


def _name_from_tags(tags: dict[str, Any]) -> str | None:
    for key in ("name:zh-Hans", "name:zh", "name", "int_name", "brand:zh", "brand"):
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _classify_tags(tags: dict[str, Any], fallback_name: str = "") -> tuple[str | None, str | None]:
    """Return ``(semantic_category, osm_kind)`` for a tagged OSM element."""

    name = fallback_name or _name_from_tags(tags) or ""
    amenity = str(tags.get("amenity") or "").strip().lower()
    office = str(tags.get("office") or "").strip().lower()
    shop = str(tags.get("shop") or "").strip().lower()
    leisure = str(tags.get("leisure") or "").strip().lower()
    tourism = str(tags.get("tourism") or "").strip().lower()
    railway = str(tags.get("railway") or "").strip().lower()
    aeroway = str(tags.get("aeroway") or "").strip().lower()
    building = str(tags.get("building") or "").strip().lower()
    landuse = str(tags.get("landuse") or "").strip().lower()
    place = str(tags.get("place") or "").strip().lower()
    healthcare = str(tags.get("healthcare") or "").strip().lower()

    if amenity in {"university", "college"}:
        return "学校", amenity
    if amenity in {"school", "kindergarten", "driving_school", "language_school"}:
        return "学校", amenity
    if amenity in {"library"}:
        return "图书馆", amenity
    if amenity in {"hospital", "clinic", "doctors", "dentist", "pharmacy"} or healthcare:
        return "医院/医疗", amenity or healthcare
    if amenity in {"cafe", "restaurant", "fast_food", "food_court", "ice_cream", "bar", "pub"}:
        return "餐饮店", amenity
    if amenity in {"bank", "atm", "bureau_de_change"}:
        return "银行", amenity
    if amenity in {"gym", "fitness_centre", "swimming_pool", "sports_centre"}:
        return "运动场所", amenity
    if amenity in {"theatre", "cinema", "arts_centre", "nightclub", "casino"}:
        return "休闲场所", amenity
    if amenity in {"bus_station", "taxi", "fuel", "parking", "charging_station"}:
        return "交通/服务点", amenity
    if amenity in {"place_of_worship"}:
        return "宗教场所", amenity
    if amenity in {"police", "fire_station", "post_office", "townhall", "courthouse"}:
        return "公共服务", amenity
    if office:
        return "办公楼/办公场所", office
    if shop:
        return "商店", shop
    if leisure in {"park", "garden", "playground", "pitch", "golf_course"}:
        return "公园/休闲区", leisure
    if leisure:
        return "休闲场所", leisure
    if tourism in {"hotel", "hostel", "guest_house", "motel", "apartment"}:
        return "酒店/住处", tourism
    if tourism:
        return "景点", tourism
    if railway in {"station", "halt", "subway_entrance", "tram_stop"}:
        return "车站", railway
    if aeroway in {"aerodrome", "terminal", "gate"}:
        return "机场", aeroway
    if building in {"dormitory"}:
        return "住处/宿舍", building
    if building in {"school", "university", "college", "kindergarten"}:
        return _classify_name(name) or "学校", building
    if building in {"office", "commercial", "retail", "industrial"}:
        return "办公楼/办公场所", building
    if building in {"apartments", "residential", "house", "detached", "terrace"}:
        return "住处/居住区", building
    if landuse in {"residential"}:
        return "住处/居住区", landuse
    if place in {"house", "neighbourhood", "suburb", "quarter", "city_block"}:
        return "居住区", place
    if building:
        # Named buildings are still useful even when the tag is generic.
        semantic = _classify_name(name)
        return semantic or "建筑", building or "building"
    return _classify_name(name), None


def _classify_name(name: str) -> str | None:
    if not name:
        return None
    if re.search(r"教[一二三四五六七八九十\d]+楼", name):
        return "教学楼"
    rules = (
        (("图书馆",), "图书馆"),
        (("食堂",), "食堂"),
        (("教学楼", "教室", "实验楼", "学院", "大学", "学校", "中学", "小学", "幼儿园"), "学校"),
        (("宿舍", "寝室"), "住处/宿舍"),
        (("公寓", "小区", "住宅", "花园", "家园", "公馆", "里"), "住处/居住区"),
        (("医院", "诊所", "卫生室", "药房", "药店"), "医院/医疗"),
        (("超市", "便利店", "菜市场", "商场", "购物中心", "广场"), "商店/商场"),
        (("咖啡",), "咖啡店"),
        (("餐厅", "饭店", "食堂", "面馆", "小吃", "烧烤", "火锅"), "餐饮店"),
        (("酒店", "宾馆", "旅馆", "民宿"), "酒店/住处"),
        (("火车站", "高铁站", "汽车站", "地铁站", "机场"), "车站/机场"),
        (("公园", "景区", "博物馆", "美术馆", "体育馆", "体育场", "游泳馆"), "休闲场所"),
        (("公司", "集团", "大厦", "写字楼", "办公楼", "产业园", "科技园"), "办公楼/办公场所"),
        (("政府", "政务", "派出所", "银行", "邮局"), "公共服务"),
    )
    for keys, label in rules:
        if any(key in name for key in keys):
            return label
    return None


@dataclass
class _ScoredPlace:
    name: str
    semantic: str | None
    kind: str | None
    score: float
    distance_m: float | None = None
    source: str = "osm"
    address: str | None = None
    enclosing: bool = False
    area_m2: float | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list, repr=False)

    def key(self) -> str:
        return f"{self.name}|{self.semantic}|{self.kind}"


class PlaceResolver:
    """Turn raw coordinates into a human place such as 学校/公司/家.

    Resolution is intentionally best-effort and cached. The public Overpass and
    Nominatim endpoints are only used when the phone reports a materially new
    location; a failure never changes the location value itself.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 4.0,
        cache_seconds: int = 900,
        overpass_url: str = "https://overpass-api.de/api/interpreter",
        osm_api_url: str = "https://api.openstreetmap.org/api/0.6/map",
        nominatim_url: str = "https://nominatim.openstreetmap.org/reverse",
        user_agent: str = "Auri/1.0 (contact: contact@example.com)",
        client: httpx.AsyncClient | None = None,
        persistent_cache_path: str | Path | None = None,
        semantic_cache_ttl_seconds: int = 7 * 24 * 3600,
        max_cache_distance_m: float = 1000.0,
    ) -> None:
        self.enabled = enabled
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.cache_seconds = max(0, int(cache_seconds))
        self.overpass_url = overpass_url
        self.osm_api_url = osm_api_url
        self.nominatim_url = nominatim_url
        self.user_agent = user_agent
        self._client = client
        self._cache: dict[
            tuple[float, float], tuple[float, float, dict[str, Any]]
        ] = {}
        self._lock = asyncio.Lock()
        self.persistent_cache_path = (
            Path(persistent_cache_path) if persistent_cache_path else None
        )
        self.semantic_cache_ttl_seconds = max(
            60, int(semantic_cache_ttl_seconds)
        )
        self.max_cache_distance_m = max(50.0, float(max_cache_distance_m))
        self._persistent_semantic: dict[str, dict[str, Any]] = {}
        self._persist_lock = threading.Lock()
        self._load_persistent_cache()

    @staticmethod
    def _cache_key(latitude: float, longitude: float) -> tuple[float, float]:
        # Cache exact fixes; cross-fix reuse additionally requires geometry.
        return (float(latitude), float(longitude))

    async def resolve(self, latitude: float, longitude: float) -> dict[str, Any]:
        latitude = float(latitude)
        longitude = float(longitude)
        now = time.monotonic()
        key = self._cache_key(latitude, longitude)
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] <= cached[1]:
            return normalize_value(dict(cached[2]))

        persistent = self._lookup_semantic_cache(latitude, longitude)
        if persistent is not None:
            # Geometry-checked reuse also avoids a network lookup for ordinary
            # GPS jitter within a known venue. Legacy entries were filtered out.
            self._cache[key] = (now, min(float(self.cache_seconds), 300.0), persistent)
            return normalize_value(dict(persistent))

        async with self._lock:
            cached = self._cache.get(key)
            if (
                cached is not None
                and time.monotonic() - cached[0] <= cached[1]
            ):
                return normalize_value(dict(cached[2]))
            data = normalize_value(await self._resolve_uncached(latitude, longitude))
            data.setdefault("resolver_version", RESOLVER_VERSION)
            data.setdefault("relation", "administrative" if data.get("semantic_source") == "administrative" else "nearby")
            if self._is_semantic_place(data):
                self._remember_semantic(latitude, longitude, data)
            else:
                cached = self._lookup_semantic_cache(latitude, longitude)
                if cached is not None:
                    # A previous successful POI/campus resolution is almost
                    # always better than regressing to an administrative label
                    # just because Overpass is rate-limiting us right now.
                    data = normalize_value(cached)
            # Administrative fallbacks should not pin a coarse city label for
            # the full TTL; retry the semantic POI lookup soon when Overpass
            # was temporarily unavailable. A semantic POI (for example a school
            # campus) is stable enough to cache for several hours, which also
            # protects replies from an intermittently slow public Overpass API.
            ttl = float(self.cache_seconds)
            if data.get("semantic_source") == "administrative":
                ttl = min(ttl, 120.0)
            elif self._is_semantic_place(data):
                ttl = max(ttl, 6 * 3600.0)
            elif data.get("cache_age_seconds") is not None:
                ttl = min(ttl, 300.0)
            self._cache[key] = (time.monotonic(), ttl, data)
            return normalize_value(dict(data))

    @staticmethod
    def _is_semantic_place(data: dict[str, Any]) -> bool:
        return bool(
            data.get("place_name")
            and data.get("semantic_source") != "administrative"
        )

    async def _resolve_uncached(self, latitude: float, longitude: float) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        if self.enabled:
            # The official OSM map API is much more reliable from mainland
            # China than the public Overpass instances and still contains the
            # local building/POI ways we need. Prefer it; use Overpass as the
            # secondary source for relation-only campuses and unusual tags.
            for resolver in (self._resolve_osm_api, self._resolve_overpass):
                try:
                    resolved = await resolver(latitude, longitude)
                except Exception as exc:  # noqa: BLE001 - best-effort enrichment
                    logger.debug("%s failed: %s", resolver.__name__, exc)
                    continue
                if resolved.get("place_name"):
                    fallback.update(resolved)
                    break
        try:
            address = await self._resolve_address(latitude, longitude)
            if address:
                if not fallback.get("place_name"):
                    fallback.update(address)
                else:
                    for key in ("address", "city", "district", "region", "country"):
                        if fallback.get(key) is None and address.get(key):
                            fallback[key] = address[key]
        except Exception as exc:  # noqa: BLE001
            logger.debug("address place resolve failed: %s", exc)
        if not fallback:
            return {
                "available": False,
                "latitude": latitude,
                "longitude": longitude,
                "reason": "no_place_resolved",
            }
        fallback.update(
            {
                "available": True,
                "latitude": latitude,
                "longitude": longitude,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return fallback

    @staticmethod
    def _point_in_polygon(
        latitude: float,
        longitude: float,
        coordinates: list[tuple[float, float]],
    ) -> bool:
        if len(coordinates) < 3:
            return False
        inside = False
        count = len(coordinates)
        for index in range(count):
            first_lat, first_lon = coordinates[index]
            second_lat, second_lon = coordinates[(index + 1) % count]
            if (first_lon > longitude) != (second_lon > longitude):
                intersection_lat = (
                    (second_lat - first_lat)
                    * (longitude - first_lon)
                    / (second_lon - first_lon)
                    + first_lat
                )
                if latitude < intersection_lat:
                    inside = not inside
        return inside

    @staticmethod
    def _polygon_area_m2(
        coordinates: list[tuple[float, float]],
    ) -> float | None:
        if len(coordinates) < 3:
            return None
        mean_lat = sum(item[0] for item in coordinates) / len(coordinates)
        latitude_scale = 111_320.0
        longitude_scale = 111_320.0 * max(0.2, math.cos(math.radians(mean_lat)))
        points = [
            (lon * longitude_scale, lat * latitude_scale)
            for lat, lon in coordinates
        ]
        area = 0.0
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0

    @classmethod
    def _select_osm_api_places(
        cls,
        places: list["_ScoredPlace"],
        latitude: float,
        longitude: float,
    ) -> list["_ScoredPlace"]:
        containing = [place for place in places if place.enclosing]
        if containing:
            # Inside a polygon, the smallest containing area is normally the
            # most specific answer (library inside university, apartment
            # building inside a residential area, office inside a campus).
            containing.sort(
                key=lambda place: (
                    place.area_m2
                    if place.area_m2 is not None
                    else float("inf"),
                    place.distance_m if place.distance_m is not None else 9999,
                )
            )
            selected = [containing[0]]
            parent_candidates = [
                place
                for place in containing[1:]
                if place.name != selected[0].name
                and place.semantic
                in {"学校", "住处/居住区", "办公楼/办公场所", "医院/医疗"}
                and _contains_place(place, selected[0])
                and not (
                    selected[0].semantic == "学校"
                    and place.semantic == "学校"
                )
            ]
            parent_candidates.sort(
                key=lambda place: -(place.area_m2 or 0.0)
            )
            if parent_candidates:
                selected.append(parent_candidates[0])
            for place in sorted(
                places,
                key=lambda item: (
                    item.distance_m is None,
                    item.distance_m or 0,
                    -item.score,
                ),
            ):
                if place in selected:
                    continue
                selected.append(place)
                if len(selected) >= 3:
                    break
            return selected[:3]

        trusted = cls._trusted_fast_places(places)
        if trusted:
            return trusted[:3]
        return []

    async def _resolve_osm_api(
        self,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any]:
        delta = 0.0025
        bbox = (
            f"{longitude - delta},{latitude - delta},"
            f"{longitude + delta},{latitude + delta}"
        )
        response = await self._get(self.osm_api_url, {"bbox": bbox})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        nodes: dict[str, tuple[float, float]] = {}
        for node in root.findall("node"):
            node_id = node.get("id")
            if node_id is None:
                continue
            try:
                nodes[node_id] = (
                    float(node.get("lat")),
                    float(node.get("lon")),
                )
            except (TypeError, ValueError):
                continue

        places: list[_ScoredPlace] = []
        for way in root.findall("way"):
            tags = {
                tag.get("k"): tag.get("v")
                for tag in way.findall("tag")
                if tag.get("k")
            }
            name = _name_from_tags(tags)
            if not name:
                continue
            semantic, kind = _classify_tags(tags, name)
            if not semantic:
                continue
            coordinates = [
                nodes[node.get("ref")]
                for node in way.findall("nd")
                if node.get("ref") in nodes
            ]
            if not coordinates:
                continue
            center_lat = sum(item[0] for item in coordinates) / len(coordinates)
            center_lon = sum(item[1] for item in coordinates) / len(coordinates)
            distance = _distance_m(latitude, longitude, center_lat, center_lon)
            enclosing = bool(len(coordinates) >= 4 and coordinates[0] == coordinates[-1] and self._point_in_polygon(latitude, longitude, coordinates))
            score = _kind_score(kind, semantic)
            if enclosing:
                score += 1.0
            places.append(
                _ScoredPlace(
                    name=name,
                    semantic=semantic,
                    kind=kind,
                    score=score,
                    distance_m=distance,
                    source="osm_api",
                    enclosing=enclosing,
                    area_m2=self._polygon_area_m2(coordinates),
                    polygon=coordinates if len(coordinates) >= 4 and coordinates[0] == coordinates[-1] else [],
                )
            )

        for node in root.findall("node"):
            tags = {
                tag.get("k"): tag.get("v")
                for tag in node.findall("tag")
                if tag.get("k")
            }
            name = _name_from_tags(tags)
            if not name:
                continue
            semantic, kind = _classify_tags(tags, name)
            if not semantic:
                continue
            try:
                node_lat = float(node.get("lat"))
                node_lon = float(node.get("lon"))
            except (TypeError, ValueError):
                continue
            places.append(
                _ScoredPlace(
                    name=name,
                    semantic=semantic,
                    kind=kind,
                    score=_kind_score(kind, semantic),
                    distance_m=_distance_m(latitude, longitude, node_lat, node_lon),
                    source="osm_api",
                )
            )

        selected = self._select_osm_api_places(places, latitude, longitude)
        if not selected:
            raise ValueError("no classified OSM API place")
        return _place_result(selected, latitude, longitude, source="osm_api")

    async def _overpass_elements(self, query: str) -> list[dict[str, Any]]:
        response = await self._post(self.overpass_url, {"data": query})
        response.raise_for_status()
        payload = response.json()
        return payload.get("elements") or []

    def _elements_to_places(
        self,
        elements: list[dict[str, Any]],
        latitude: float,
        longitude: float,
    ) -> tuple[list[_ScoredPlace], list[_ScoredPlace]]:
        enclosing: list[_ScoredPlace] = []
        nearby: list[_ScoredPlace] = []
        for element in elements:
            tags = element.get("tags") or {}
            if not isinstance(tags, dict):
                continue
            name = _name_from_tags(tags)
            if not name:
                continue
            semantic, kind = _classify_tags(tags, name)
            if not semantic:
                continue
            if element.get("type") in {"way", "relation"} and tags.get("boundary"):
                # Administrative boundaries are useful as fallback areas only.
                continue
            lat = element.get("lat")
            lon = element.get("lon")
            center = element.get("center") or {}
            if lat is None:
                lat = center.get("lat")
                lon = center.get("lon")
            distance = None
            if lat is not None and lon is not None:
                distance = _distance_m(latitude, longitude, float(lat), float(lon))
            score = _kind_score(kind, semantic)
            if score <= 0:
                continue
            place = _ScoredPlace(
                name=name,
                semantic=semantic,
                kind=kind,
                score=score,
                distance_m=distance,
                polygon=[(float(p["lat"]), float(p["lon"])) for p in element.get("geometry", []) if "lat" in p and "lon" in p],
            )
            place.enclosing = bool(place.polygon and self._point_in_polygon(latitude, longitude, place.polygon))
            place.area_m2 = self._polygon_area_m2(place.polygon)
            if place.enclosing:
                place.score += 0.5
                enclosing.append(place)
            else:
                if distance is not None and distance <= 220:
                    nearby.append(place)
        return enclosing, nearby

    @staticmethod
    def _trusted_fast_places(
        places: list[_ScoredPlace],
    ) -> list[_ScoredPlace]:
        """Pick only nearby POIs that are safe to treat as the current place.

        A distant restaurant should not make us claim the user is at a
        restaurant. Campus/hospital polygons are trusted over a wider radius;
        point-like venues need the user to be very close.
        """
        trusted: list[_ScoredPlace] = []
        for place in places:
            distance = place.distance_m
            semantic = place.semantic
            if semantic in {"学校", "医院/医疗"}:
                limit = 350.0
            elif semantic in {
                "图书馆",
                "食堂",
                "办公楼/办公场所",
                "住处/居住区",
                "住处/宿舍",
                "公园/休闲区",
            }:
                limit = 150.0
            elif semantic in {
                "餐饮店",
                "商店",
                "商店/商场",
                "咖啡店",
                "酒店/住处",
                "运动场所",
                "银行",
                "车站",
            }:
                limit = 80.0
            else:
                continue
            if place.enclosing or (distance is not None and distance <= limit):
                trusted.append(place)
        trusted.sort(
            key=lambda place: (
                -(place.score),
                place.distance_m if place.distance_m is not None else 9999,
            )
        )
        return trusted

    @classmethod
    def _select_places(
        cls,
        enclosing: list[_ScoredPlace],
        nearby: list[_ScoredPlace],
    ) -> list[_ScoredPlace]:
        if enclosing:
            return cls._select_osm_api_places([*enclosing, *nearby], 0, 0)
        selected: list[_ScoredPlace] = []
        for place in sorted(
            nearby,
            key=lambda item: (
                item.distance_m is None,
                item.distance_m or 0,
                -item.score,
            ),
        ):
            if any(item.name == place.name for item in selected):
                continue
            selected.append(place)
            if len(selected) >= 3:
                break
        return selected

    async def _resolve_overpass(self, latitude: float, longitude: float) -> dict[str, Any]:
        # Fast pass: a simple amenity query is dramatically more reliable than
        # the is_in + regex query on public Overpass instances. It still gives
        # campus-level answers such as 北京师范大学 or 海淀区医院.
        fast_query = (
            "[out:json][timeout:6];"
            f"nwr(around:260,{latitude},{longitude})[name][amenity];"
            "out center 30;"
        )
        fast_elements = await self._overpass_elements(fast_query)
        fast_enclosing, fast_nearby = self._elements_to_places(
            fast_elements,
            latitude,
            longitude,
        )
        trusted = self._trusted_fast_places([*fast_enclosing, *fast_nearby])
        if trusted:
            return _place_result(
                trusted[:3],
                latitude,
                longitude,
                source="osm",
            )

        # The fast query worked but was not specific enough (for example only
        # an adjacent shop). Ask the richer query for building/POI precision.
        rich_query = (
            "[out:json][timeout:6];"
            f"is_in({latitude},{longitude})->.a;"
            ".a out tags center 15;"
            f'nwr(around:170,{latitude},{longitude})[name]'
            '[~"^(amenity|office|shop|leisure|tourism|railway|aeroway|'
            'building|landuse|place|healthcare)$"~"."];'
            "out center 25;"
        )
        elements = await self._overpass_elements(rich_query)
        enclosing, nearby = self._elements_to_places(
            elements,
            latitude,
            longitude,
        )
        selected = self._select_places(enclosing, nearby)
        if not selected:
            raise ValueError("no classified OSM place")
        return _place_result(selected, latitude, longitude, source="osm")

    def _load_persistent_cache(self) -> None:
        if self.persistent_cache_path is None:
            return
        try:
            payload = json.loads(
                self.persistent_cache_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, dict):
            self._persistent_semantic = {
                str(key): value
                for key, value in entries.items()
                if isinstance(value, dict) and value.get("resolver_version") == RESOLVER_VERSION
            }

    def _save_persistent_cache(self) -> None:
        if self.persistent_cache_path is None:
            return
        try:
            self.persistent_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.persistent_cache_path.with_suffix(
                self.persistent_cache_path.suffix + ".tmp"
            )
            temporary.write_text(
                json.dumps(
                    {"version": RESOLVER_VERSION, "entries": self._persistent_semantic},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.persistent_cache_path)
        except OSError:
            logger.debug("failed to persist semantic place cache", exc_info=True)

    def _remember_semantic(
        self,
        latitude: float,
        longitude: float,
        data: dict[str, Any],
    ) -> None:
        if self.persistent_cache_path is None:
            return
        key = f"{round(float(latitude), 4)},{round(float(longitude), 4)}"
        entry = {
            field: data.get(field)
            for field in (
                "place_name",
                "place_type",
                "place_label",
                "parent_name",
                "parent_type",
                "address",
                "city",
                "district",
                "region",
                "country",
                "source",
                "confidence",
                "resolver_version",
                "relation",
                "_polygon",
            )
        }
        entry.update(
            {
                "latitude": float(latitude),
                "longitude": float(longitude),
                "stored_at": time.time(),
            }
        )
        with self._persist_lock:
            self._persistent_semantic[key] = entry
            self._save_persistent_cache()

    def _lookup_semantic_cache(
        self,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any] | None:
        if not self._persistent_semantic:
            return None
        now = time.time()
        best: tuple[float, dict[str, Any]] | None = None
        for entry in self._persistent_semantic.values():
            if not isinstance(entry, dict) or not entry.get("place_name"):
                continue
            if entry.get("resolver_version") != RESOLVER_VERSION:
                continue
            stored_at = float(entry.get("stored_at") or 0)
            if now - stored_at > self.semantic_cache_ttl_seconds:
                continue
            try:
                distance = _distance_m(
                    latitude,
                    longitude,
                    float(entry.get("latitude")),
                    float(entry.get("longitude")),
                )
            except (TypeError, ValueError):
                continue
            if distance > self.max_cache_distance_m:
                continue
            polygon = entry.get("_polygon") or []
            # Exact fix reuse is safe as a cached observation. A different fix
            # must lie in the original polygon; proximity alone proves no venue.
            if distance > 0.2 and not (polygon and self._point_in_polygon(latitude, longitude, polygon)):
                continue
            if best is None or distance < best[0]:
                best = (distance, entry)
        if best is None:
            return None
        distance, entry = best
        result = {
            field: entry.get(field)
            for field in (
                "place_name",
                "place_type",
                "place_label",
                "parent_name",
                "parent_type",
                "address",
                "city",
                "district",
                "region",
                "country",
                "confidence",
                "resolver_version",
                "relation",
                "_polygon",
            )
        }
        result.update(
            {
                "available": True,
                "source": "semantic_cache",
                "cache_version": RESOLVER_VERSION,
                "semantic_source": "persistent_cache",
                "cache_age_seconds": round(now - float(entry.get("stored_at") or now)),
                "cache_distance_m": round(distance),
            }
        )
        return result

    async def _resolve_address(self, latitude: float, longitude: float) -> dict[str, Any] | None:
        # BigDataCloud first (cheap administrative context), Nominatim second.
        try:
            response = await self._get(
                "https://api.bigdatacloud.net/data/reverse-geocode-client",
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "localityLanguage": "zh",
                },
            )
            response.raise_for_status()
            data = response.json()
            if data:
                city = data.get("city") or data.get("locality")
                district = data.get("locality")
                region = data.get("principalSubdivision")
                country = data.get("countryName")
                address_parts = list(
                    dict.fromkeys(
                        part
                        for part in (district, city, region, country)
                        if part
                    )
                )
                label = "，".join(address_parts) if address_parts else None
                if label:
                    semantic = "区域"
                    return {
                        "place_name": city or region,
                        "place_type": semantic,
                        "place_label": label,
                        "address": label,
                        "city": city,
                        "district": district,
                        "region": region,
                        "country": country,
                        "source": "bigdatacloud",
                        "confidence": 0.45,
                        "semantic_source": "administrative",
                    }
        except Exception:
            pass

        try:
            response = await self._get(
                self.nominatim_url,
                {
                    "format": "jsonv2",
                    "lat": latitude,
                    "lon": longitude,
                    "zoom": 18,
                    "addressdetails": 1,
                    "accept-language": "zh-CN",
                },
            )
            response.raise_for_status()
            data = response.json()
            address = data.get("address") or {}
            parts = [
                address.get("city"),
                address.get("suburb") or address.get("district"),
                address.get("road"),
                address.get("house_number"),
            ]
            label = "".join(str(part) for part in parts if part)
            display = data.get("display_name") or label
            if display:
                name = data.get("name") or address.get("university") or address.get(
                    "school"
                ) or address.get("amenity") or address.get("road")
                return {
                    "place_name": name,
                    "place_type": "区域",
                    "place_label": display,
                    "address": display,
                    "city": address.get("city"),
                    "district": address.get("suburb") or address.get("district"),
                    "region": address.get("state"),
                    "country": address.get("country"),
                    "source": "nominatim",
                    "confidence": 0.4,
                    "semantic_source": "administrative",
                }
        except Exception:
            pass
        return None

    async def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        headers = {"User-Agent": self.user_agent}
        if self._client is not None:
            return await self._client.get(url, params=params, headers=headers)
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            return await client.get(url, params=params)

    async def _post(self, url: str, data: dict[str, Any]) -> httpx.Response:
        headers = {"User-Agent": self.user_agent}
        if self._client is not None:
            return await self._client.post(url, data=data, headers=headers)
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            return await client.post(url, data=data)


def _kind_score(kind: str | None, semantic: str | None) -> float:
    if kind in {"university", "college", "school", "kindergarten"}:
        return 5.0
    if semantic in {"办公楼/办公场所", "住处/居住区", "医院/医疗"}:
        return 4.0
    if semantic in {"餐饮店", "商店", "商店/商场", "车站", "机场", "酒店/住处"}:
        return 3.0
    if semantic in {"图书馆", "食堂"}:
        return 3.5
    if semantic:
        return 2.0
    return 0.0


def _contains_place(parent: _ScoredPlace, child: _ScoredPlace) -> bool:
    """Prove polygon coverage, including concave boundaries, before parenting."""
    if not (parent.enclosing and child.enclosing and parent.polygon and child.polygon):
        return False
    if not parent.area_m2 or not child.area_m2 or parent.area_m2 <= child.area_m2:
        return False
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    def on_segment(a, b, p):
        return abs(cross(a, b, p)) < 1e-12 and min(a[0], b[0])-1e-12 <= p[0] <= max(a[0], b[0])+1e-12 and min(a[1], b[1])-1e-12 <= p[1] <= max(a[1], b[1])+1e-12
    edges = list(zip(parent.polygon, parent.polygon[1:]+parent.polygon[:1]))
    for point in child.polygon:
        if not PlaceResolver._point_in_polygon(*point, parent.polygon) and not any(on_segment(a, b, point) for a, b in edges):
            return False
    for a, b in zip(child.polygon, child.polygon[1:]+child.polygon[:1]):
        for c, d in edges:
            if cross(a,b,c)*cross(a,b,d) < -1e-24 and cross(c,d,a)*cross(c,d,b) < -1e-24:
                return False
        # A segment may leave a concave parent exactly through vertices without
        # a proper crossing. Split at those vertices and check each interval.
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = dx*dx + dy*dy
        if length:
            cuts = sorted({0.0, 1.0, *(((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length for p in parent.polygon if on_segment(a,b,p))})
            for t1,t2 in zip(cuts,cuts[1:]):
                t=(t1+t2)/2;point=(a[0]+t*dx,a[1]+t*dy)
                if not PlaceResolver._point_in_polygon(*point,parent.polygon) and not any(on_segment(c,d,point) for c,d in edges):
                    return False
    return True


def _place_result(
    places: list[_ScoredPlace],
    latitude: float,
    longitude: float,
    *,
    source: str,
) -> dict[str, Any]:
    primary = places[0]
    parent = next(
        (
            place
            for place in places[1:]
            if place.name != primary.name
            and place.semantic in {"学校", "住处/居住区", "办公楼/办公场所"}
            and _contains_place(place, primary)
            # A campus polygon and a college inside the same campus are both
            # "学校"; showing the smaller one as the parent reads backwards.
            and not (
                primary.semantic == "学校" and place.semantic == "学校"
            )
        ),
        None,
    )
    label_parts = []
    if primary.semantic:
        label_parts.append(f"{primary.name}（{primary.semantic}）")
    else:
        label_parts.append(primary.name)
    if parent and parent.semantic:
        if primary.semantic == "建筑":
            label_parts = [
                f"{primary.name}（{parent.semantic}内建筑）",
                f"位于{parent.name}",
            ]
        else:
            label_parts.append(f"位于{parent.name}（{parent.semantic}）")
    label = "，".join(label_parts)
    relation = "contains" if primary.enclosing and primary.polygon else "nearby"
    if relation == "nearby":
        label = "附近场所：" + label
    return {
        "place_name": primary.name,
        "place_type": primary.semantic,
        "place_label": label,
        "parent_name": parent.name if parent else None,
        "parent_type": parent.semantic if parent else None,
        "address": None,
        "city": None,
        "district": None,
        "region": None,
        "country": None,
        "source": source,
        "resolver_version": RESOLVER_VERSION,
        "relation": relation,
        "_polygon": primary.polygon,
        "confidence": 0.8 if primary.semantic else 0.5,
        "candidates": [
            {
                "name": place.name,
                "type": place.semantic,
                "kind": place.kind,
                "distance_m": round(place.distance_m) if place.distance_m is not None else None,
            }
            for place in places[:3]
        ],
    }


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))
