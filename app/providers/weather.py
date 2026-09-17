class WeatherProvider:
    name = "weather"

    async def get_weather(
        self,
        location_name: str | None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict:
        # TODO: Connect the weather API.
        return {}
