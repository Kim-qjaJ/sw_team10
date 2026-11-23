"""전시장 정기 휴관일 읽기와 무료 전시 결과에서 빼기 (2026-11-23 '월요일 휴관 박물관이 나온다' 지적)."""

from datetime import date

from app.providers import rest_days
from app.providers.rest_days import clean_rest, closed_on
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.hard_filter import HardFilter

MONDAY, TUESDAY = date(2026, 11, 23), date(2026, 11, 24)


def test_weekly_rules():
    rest = "매주 월요일 (단, 월요일이 공휴일인 경우 그 다음날 휴관) / 1월 1일"
    assert closed_on(rest, MONDAY) is True
    assert closed_on(rest, TUESDAY) is False
    assert closed_on(rest, date(2027, 1, 1)) is True          # 금요일이지만 1월 1일
    assert closed_on("매주 월, 화요일", TUESDAY) is True
    assert closed_on("화요일 휴관", TUESDAY) is True
    assert closed_on("연중무휴", MONDAY) is False
    assert closed_on(None, MONDAY) is None                     # 모르면 빼지 않는다


def test_html_is_cleaned():
    assert clean_rest("매주 월요일<br>1월 1일 &amp; 설날") == "매주 월요일 / 1월 1일 & 설날"


def test_free_exhibit_drops_closed_today_only():
    req = PlaceRequest(query="무료 전시", category="culture")
    events = [{"title": "전시", "start": "2026-11-01", "end": "2026-12-31", "free": True}]
    closed = Place(name="국립해양박물관", category="culture", events=events, closed_today=True, rest_info="매주 월요일")
    unknown = Place(name="부산현대미술관", category="culture", events=events, closed_today=None)
    open_ = Place(name="부산시민공원역사관", category="culture", events=events, closed_today=False)
    kept = [p.name for p in HardFilter().apply(req, [closed, unknown, open_])]
    assert kept == ["부산현대미술관", "부산시민공원역사관"]
    # 전시 요청이 아니면 휴관 여부로 거르지 않는다
    cafe = PlaceRequest(query="카페", category="cafe")
    assert HardFilter().apply(cafe, [Place(name="카페", category="cafe", closed_today=True)])


def test_cached_lookup_marks_place(monkeypatch):
    monkeypatch.setattr(rest_days, "_cache", {"국립해양박물관": {"rest": "매주 월요일", "at": 9e12}})
    assert rest_days.cached_rest("국립 해양박물관") == (True, "매주 월요일")
    assert rest_days.cached_rest("부산현대미술관") == (False, None)
