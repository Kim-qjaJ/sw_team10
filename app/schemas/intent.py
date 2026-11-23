from typing import Literal

from pydantic import BaseModel, Field, model_validator

IntentType = Literal["recommend_place","search_place","get_event","get_route","smalltalk","place_detail","compare_results","clarify","unsupported"]
# requests가 비어 있어도 되는 Intent
NO_REQUEST_INTENTS = {"smalltalk","place_detail","compare_results","clarify","unsupported"}
PlaceCategory = Literal["restaurant","cafe","tourism","culture","activity","public_facility","education","other"]
TransportMode = Literal["walk","transit","car"]
ResultScope = Literal["default","all"]
SearchMode = Literal["category","brand","exact_place"]

class PlaceRequest(BaseModel):
    """One place/data request extracted from the user's natural language."""
    query: str = Field(min_length=1,max_length=200)
    category: PlaceCategory|None=None
    subcategory: str|None=None
    indoor: bool|None=None
    result_scope: ResultScope="default"
    search_mode: SearchMode="category"

class UserIntent(BaseModel):
    intent: IntentType
    location: str|None=None
    requests:list[PlaceRequest]=Field(default_factory=list)
    companion:Literal["alone","friend","family"]|None=None
    transport_mode:TransportMode|None=None
    # '걸어서 5분 이내'의 5. transport_mode와 함께 결과를 거르는 이동 시간 상한(분). 후속 질문에도 이어진다.
    max_travel_minutes:int|None=Field(default=None,ge=1,le=180)
    # intent="clarify"일 때 사용자에게 되물을 질문
    question:str|None=None

    @model_validator(mode="after")
    def check_requests(self)->"UserIntent":
        if self.intent not in NO_REQUEST_INTENTS and not self.requests:
            raise ValueError("requests must contain at least one item")
        return self
