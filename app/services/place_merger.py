from app.schemas.place import Place


class PlaceMerger:
    """Merge duplicated places from multiple providers.

    Final matching should combine normalized name, address and coordinate
    distance. The conservative key below is only a structural placeholder.
    """

    def merge(self, places: list[Place]) -> list[Place]:
        merged: dict[tuple[str, str], Place] = {}

        for place in places:
            key = (
                place.name.strip().lower(),
                (place.address or "").strip().lower(),
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = place.model_copy(deep=True)
                continue

            existing.sources = sorted(set(existing.sources + place.sources))
            existing.source_ids.update(place.source_ids)
            existing.busan_verified = (
                existing.busan_verified or place.busan_verified
            )

        return list(merged.values())
