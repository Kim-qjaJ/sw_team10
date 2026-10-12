"""기상청 초단기실황 날씨 조회.

[API 담당] 인터페이스만 정의된 뼈대입니다. ContextService.weather()가 이 클래스를 사용합니다.
"""


class WeatherProvider:
    name = "weather"

    async def get_weather(self, latitude: float, longitude: float) -> dict | None:
        """해당 좌표의 현재 날씨. 예: {"is_raining": False, "temperature": 18.2, "source": "kma"}

        TODO: 기상청 API를 호출한다. 실패하거나 설정이 없으면 None을 반환한다.
        """
        return None
