"""부산 지역 이름과 경계 색인. 앱 프로세스에서 한 번 로드하고 LLM에 보내지 않는다."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import re

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "regions"
BOUNDARY_TOLERANCE_M = 50
# 이름이 같은 지역이 여러 구에 있을 때 되묻지 않고 고르는 기본값.
# 다른 쪽을 원하면 '강서구 송정동'처럼 구를 함께 적으면 된다.
DEFAULT_DISTRICT = {"송정": "해운대구", "송정동": "해운대구"}
LEVEL_DEPTH = {"sgg": 0, "hdong": 1, "bdong": 1, "ri": 2}


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    level: str
    sgg: str
    parent: str
    aliases: tuple[str, ...]
    latitude: float
    longitude: float
    coord_source: str
    related: tuple[str, ...]
    radius_m: int = 0

    @property
    def qualified_name(self) -> str:
        parts = ["부산", self.sgg]
        if self.level == "ri":
            parts.append(self.parent)
        if self.level != "sgg":
            parts.append(self.name)
        return " ".join(parts)

    @property
    def accuracy(self) -> str:
        exact = {"boundary", "same_name_hdong"}
        return "administrative_representative" if self.coord_source in exact else "approximate"


@dataclass(frozen=True)
class LifestyleArea:
    """행정구역이 아닌 생활권(서면, 센텀, 광안리 등).

    좌표는 지어내지 않는다. 실행 중 anchor_query(기준 시설)를 카카오로 조회해 쓰고,
    조회할 수 없으면 fallback 행정구역의 대표점을 근사값으로 쓴다.
    anchor_query가 없으면 fallback 행정구역 자체로 검색한다.
    """

    name: str
    aliases: tuple[str, ...]
    sgg: str
    fallback: Region
    anchor_query: str | None
    radius_m: int
    note: str = ""


@dataclass(frozen=True)
class RegionMatch:
    region: Region | None = None
    candidates: tuple[Region, ...] = ()

    @property
    def question(self) -> str | None:
        if not self.candidates:
            return None
        choices = " / ".join(dict.fromkeys(region.qualified_name.removeprefix("부산 ") for region in self.candidates))
        return f"어느 지역을 찾을까요? {choices} 중에서 지역 이름을 알려주세요."


def clean_location(text: str) -> str:
    return re.sub(r"^(부산광역시|부산시|부산)\s*", "", text.strip()).strip()


def _ring_contains(x: float, y: float, ring: list) -> bool:
    inside = False
    for a, b in zip(ring, ring[1:]):
        # 단순화된 경계 위의 좌표는 포함한다.
        cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-10 and min(a[0], b[0]) - 1e-8 <= x <= max(a[0], b[0]) + 1e-8 and min(a[1], b[1]) - 1e-8 <= y <= max(a[1], b[1]) + 1e-8:
            return True
        if (a[1] > y) != (b[1] > y):
            if x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
    return inside


class RegionRegistry:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.regions: dict[str, Region] = {}
        self.aliases: dict[str, list[Region]] = {}
        self.polygons: dict[str, list[tuple[tuple[float, float, float, float], list]]] = {}
        self.district_codes: dict[str, list[str]] = {}
        with (data_dir / "busan_regions.csv").open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                region = Region(
                    code=row["code"], name=row["name"], level=row["level"], sgg=row["sgg"], parent=row["parent"],
                    aliases=tuple(filter(None, row["aliases"].split("|"))), latitude=float(row["lat"]), longitude=float(row["lng"]),
                    coord_source=row["coord_source"], related=tuple(filter(None, row["related"].split("|"))),
                    radius_m=int(float(row["radius_m"] or 0)),
                )
                if region.code in self.regions:
                    raise ValueError(f"Duplicate region code: {region.code}")
                self.regions[region.code] = region
                for name in {region.name, *region.aliases}:
                    self.aliases.setdefault(name, []).append(region)
        geo = json.loads((data_dir / "busan_hdong.geojson").read_text(encoding="utf-8-sig"))
        for feature in geo["features"]:
            code, sgg = feature["properties"]["code"], feature["properties"]["sgg"]
            geometry = feature["geometry"]
            polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
            self.polygons[code] = []
            for polygon in polygons:
                points = [point for ring in polygon for point in ring]
                bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
                self.polygons[code].append((bounds, polygon))
            self.district_codes.setdefault(sgg, []).append(code)
        if set(self.polygons) != {r.code for r in self.regions.values() if r.level == "hdong"}:
            raise ValueError("CSV and GeoJSON administrative codes do not match")
        self.lifestyle_areas: dict[str, LifestyleArea] = {}
        lifestyle_path = data_dir / "busan_lifestyle.csv"
        if lifestyle_path.exists():
            with lifestyle_path.open(encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    fallback = self._official(row["fallback_region"])
                    if fallback is None or fallback.sgg != row["sgg"]:
                        raise ValueError(f"Unknown lifestyle fallback region: {row['name']} -> {row['fallback_region']}")
                    area = LifestyleArea(
                        name=row["name"], aliases=tuple(filter(None, row["aliases"].split("|"))), sgg=row["sgg"],
                        fallback=fallback, anchor_query=row["anchor_query"].strip() or None,
                        radius_m=int(row["radius_m"] or 0) or fallback.radius_m, note=row.get("note", ""),
                    )
                    for name in (area.name, *area.aliases):
                        self.lifestyle_areas[name] = area

    def _official(self, text: str) -> Region | None:
        """'부산진구 부전2동'처럼 구와 이름을 함께 적은 공식 지역을 찾는다."""
        parts = text.split()
        sgg, name = (parts[0], parts[-1]) if len(parts) > 1 else (parts[0], parts[0])
        found = [r for r in self.aliases.get(name, []) if r.sgg == sgg and (len(parts) > 1 or r.level == "sgg")]
        found.sort(key=lambda r: {"hdong": 0, "sgg": 1, "bdong": 2, "ri": 3}[r.level])
        return found[0] if found else None

    def lifestyle(self, location: str | None) -> LifestyleArea | None:
        """'부산 서면' → 서면 생활권. 행정구역 이름(예: 해운대구)은 여기서 찾지 않는다."""
        if not location:
            return None
        return self.lifestyle_areas.get(clean_location(location))

    @property
    def lifestyle_names(self) -> set[str]:
        """기준 시설이 있는 생활권 이름. 이 이름 하나만 오면 구 전체로 해석하지 않는다."""
        return {name for name, area in self.lifestyle_areas.items() if area.anchor_query}

    @property
    def names(self) -> dict[str, list[Region]]:
        return self.aliases

    def _context_district(self, context: str | None) -> str | None:
        if not context:
            return None
        text = clean_location(context)
        for district in self.district_codes:
            if district in text:
                return district
        candidates = self.aliases.get(text, [])
        districts = {r.sgg for r in candidates}
        return next(iter(districts)) if len(districts) == 1 else None

    def match_names(self, names: list[str], context: str | None = None) -> RegionMatch:
        names = list(dict.fromkeys(name for name in names if name != "부산"))
        if not names or any(name not in self.aliases for name in names):
            return RegionMatch()
        if len(names) == 1 and names[0] in self.lifestyle_names:
            return RegionMatch()
        explicit_districts = {r.sgg for name in names for r in self.aliases[name] if r.level == "sgg" and name == r.name}
        if len(explicit_districts) > 1:
            return RegionMatch()
        district = next(iter(explicit_districts), None) or self._context_district(context)
        pool = [r for name in names for r in self.aliases[name] if not explicit_districts or r.sgg in explicit_districts]
        if district and any(r.sgg == district for r in pool):
            pool = [r for r in pool if r.sgg == district]
        # 상위 지역을 같이 적었으면 하위 지역만 선택한다. 다른 지역 둘을 합치지는 않는다.
        if len(names) > 1:
            children = [r for r in pool if all(name in {r.name, *r.aliases, r.sgg, r.parent} or
                       any(parent.code in r.related or r.code in parent.related for parent in self.aliases[name]) for name in names)]
            if not children:
                return RegionMatch()
            deepest = max(LEVEL_DEPTH[r.level] for r in children)
            pool = [r for r in children if LEVEL_DEPTH[r.level] == deepest]
        # 같은 이름의 행정동/법정동은 연결된 한 후보로 묶는다.
        unique: dict[tuple[str, str], Region] = {}
        for region in pool:
            key = (region.sgg, region.name)
            existing = unique.get(key)
            if existing is None or (region.level == "hdong" and existing.level == "bdong"):
                unique[key] = region
        candidates = tuple(unique.values())
        if len(candidates) > 1:
            candidates = self._narrow(names, candidates)
        return RegionMatch(region=candidates[0]) if len(candidates) == 1 else RegionMatch(candidates=candidates)

    @staticmethod
    def _narrow(names: list[str], candidates: tuple[Region, ...]) -> tuple[Region, ...]:
        """되물을 필요가 없는 경우를 줄인다.

        - 한 후보가 나머지를 모두 포함하면(기장군 ⊃ 기장읍) 넓은 쪽으로 검색한다.
        - 기본값이 정해진 이름(송정 → 해운대구)은 그 구를 고른다.
        """
        counties = [r for r in candidates if r.level == "sgg"]
        if len(counties) == 1 and all(r.sgg == counties[0].sgg for r in candidates):
            return (counties[0],)
        if len(names) == 1 and names[0] in DEFAULT_DISTRICT:
            preferred = [r for r in candidates if r.sgg == DEFAULT_DISTRICT[names[0]]]
            if len(preferred) == 1:
                return tuple(preferred)
        return candidates

    def resolve(self, location: str | None, context: str | None = None) -> RegionMatch:
        if not location:
            return RegionMatch()
        text = clean_location(location)
        names = text.split()
        if all(name in self.aliases for name in names):
            return self.match_names(names, context)
        # '해운대구송정동' 같은 붙여 쓴 행정구역도 지원한다.
        for district in self.district_codes:
            if text.startswith(district) and text[len(district):] in self.aliases:
                return self.match_names([district, text[len(district):]], context)
        return RegionMatch()

    def boundary_codes(self, region: Region) -> tuple[list[str], str]:
        if region.level == "sgg":
            return self.district_codes.get(region.sgg, []), "administrative"
        if region.level == "hdong":
            return [region.code], "administrative"
        if region.level == "bdong":
            return [code for code in region.related if code in self.polygons], "approximate_admin_union"
        parent = next((r for r in self.regions.values() if r.level == "hdong" and r.sgg == region.sgg and r.name == region.parent), None)
        return ([parent.code] if parent else []), "parent_administrative"

    def contains(self, region: Region, latitude: float, longitude: float) -> bool | None:
        codes, _ = self.boundary_codes(region)
        if not codes:
            return None
        near_boundary = False
        xscale = 111_320 * math.cos(math.radians(latitude))
        yscale = 111_320
        for code in codes:
            for (left, bottom, right, top), polygon in self.polygons.get(code, []):
                if left <= longitude <= right and bottom <= latitude <= top:
                    if _ring_contains(longitude, latitude, polygon[0]) and not any(_ring_contains(longitude, latitude, hole) for hole in polygon[1:]):
                        return True
                if left - BOUNDARY_TOLERANCE_M / xscale <= longitude <= right + BOUNDARY_TOLERANCE_M / xscale and bottom - BOUNDARY_TOLERANCE_M / yscale <= latitude <= top + BOUNDARY_TOLERANCE_M / yscale:
                    for ring in polygon:
                        for a, b in zip(ring, ring[1:]):
                            ax, ay = (a[0] - longitude) * xscale, (a[1] - latitude) * yscale
                            bx, by = (b[0] - longitude) * xscale, (b[1] - latitude) * yscale
                            dx, dy = bx - ax, by - ay
                            denominator = dx * dx + dy * dy
                            t = min(1, max(0, -(ax * dx + ay * dy) / denominator)) if denominator else 0
                            if math.hypot(ax + t * dx, ay + t * dy) <= BOUNDARY_TOLERANCE_M:
                                near_boundary = True
        return None if near_boundary else False

    def public_info(self, region: Region) -> dict:
        return {"code": region.code, "name": region.name, "level": region.level, "district": region.sgg,
                "coordinate_source": region.coord_source, "coordinate_accuracy": region.accuracy,
                "boundary_scope": self.boundary_codes(region)[1]}


@lru_cache(maxsize=1)
def get_region_registry() -> RegionRegistry:
    return RegionRegistry()
