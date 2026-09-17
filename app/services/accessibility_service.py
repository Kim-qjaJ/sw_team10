from app.schemas.intent import TransportMode
from app.schemas.place import Place


class AccessibilityService:
    """Convert route/travel-time metadata into a maximum 25-point score."""

    MAX_SCORE = 25.0

    def score(self, place: Place, mode: TransportMode | None) -> float:
        selected_mode: TransportMode = mode or "transit"

        if selected_mode == "walk":
            return self._walk_score(place.walking_minutes)

        if selected_mode == "car":
            return self._car_score(place.driving_minutes)

        transit_score = self._transit_score(
            place.transit_minutes,
            place.transit_transfers,
        )
        if transit_score == 0.0 and place.walking_minutes is not None:
            return self._walk_score(place.walking_minutes)
        return transit_score

    def _walk_score(self, minutes: float | None) -> float:
        if minutes is None:
            return 0.0
        if minutes <= 5:
            return 25.0
        if minutes <= 10:
            return 22.0
        if minutes <= 15:
            return 18.0
        if minutes <= 20:
            return 12.0
        if minutes <= 30:
            return 6.0
        return 2.0

    def _transit_score(
        self,
        minutes: float | None,
        transfers: int | None,
    ) -> float:
        if minutes is None:
            return 0.0
        if minutes <= 10:
            score = 25.0
        elif minutes <= 20:
            score = 22.0
        elif minutes <= 30:
            score = 17.0
        elif minutes <= 40:
            score = 10.0
        else:
            score = 5.0

        transfer_penalty = max(transfers or 0, 0) * 3.0
        return max(score - transfer_penalty, 0.0)

    def _car_score(self, minutes: float | None) -> float:
        if minutes is None:
            return 0.0
        if minutes <= 10:
            return 25.0
        if minutes <= 20:
            return 20.0
        if minutes <= 30:
            return 15.0
        if minutes <= 45:
            return 10.0
        return 5.0
