"""모든 테스트 공통: 실제 API를 부르는 뒤쪽 작업은 막는다."""

import pytest

from app.providers import rest_days


@pytest.fixture(autouse=True)
def no_rest_day_lookup(monkeypatch):
    # 전시장 휴관일은 TourAPI를 부르고 파일에 저장하므로, 테스트에서는 빈 저장소로 두고 받지 않는다.
    monkeypatch.setattr(rest_days, "_cache", {})
    monkeypatch.setattr(rest_days, "_save", lambda: None)
    monkeypatch.setattr(rest_days, "fetch_in_background", lambda names: None)
