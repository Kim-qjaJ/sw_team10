from app.schemas.place import Place


class PlaceNormalizer:
    """Convert provider-specific payloads into the shared Place schema."""

    def normalize(self, provider: str, payload: dict) -> Place:
        # TODO: Implement provider-specific mapping after external APIs are connected.
        raise NotImplementedError(
            f"{provider} normalization is not implemented yet"
        )
