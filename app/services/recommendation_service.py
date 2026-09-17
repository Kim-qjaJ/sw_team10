from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.accessibility_service import AccessibilityService

SCORE_MAX = {
    "relevance": 30.0,
    "accessibility": 25.0,
    "user_condition": 15.0,
    "weather": 10.0,
    "cross_provider": 10.0,
    "busan_data": 10.0,
}


class RecommendationService:
    """Explainable 100-point recommendation model.

    External API ranking is not used as the final recommendation order.
    Route/accessibility metadata and provider data will be filled by other
    modules once those APIs are implemented.
    """

    def __init__(self) -> None:
        self.accessibility = AccessibilityService()

    def rank(
        self,
        intent: UserIntent,
        request: PlaceRequest,
        places: list[Place],
        limit: int = 5,
    ) -> list[Place]:
        ranked: list[Place] = []

        for original in places:
            place = original.model_copy(deep=True)
            breakdown: dict[str, float] = {}

            relevance = 0.0
            query = request.query.strip().lower()
            if query and query in place.name.lower():
                relevance += 15.0
            if request.category and request.category == place.category:
                relevance += 15.0
            breakdown["relevance"] = min(
                relevance,
                SCORE_MAX["relevance"],
            )

            breakdown["accessibility"] = self.accessibility.score(
                place,
                intent.transport_mode,
            )

            if request.indoor is None:
                breakdown["user_condition"] = 7.5
            elif place.indoor is request.indoor:
                breakdown["user_condition"] = 15.0
            else:
                breakdown["user_condition"] = 0.0

            breakdown["weather"] = 0.0
            breakdown["cross_provider"] = (
                10.0 if len(set(place.sources)) >= 2 else 5.0
            )
            breakdown["busan_data"] = (
                10.0 if place.busan_verified else 0.0
            )

            place.score_breakdown = breakdown
            place.score = round(sum(breakdown.values()), 2)
            ranked.append(place)

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]
