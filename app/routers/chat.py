from fastapi import APIRouter, HTTPException

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.llm_service import LLMService

router = APIRouter(prefix="/chat", tags=["chat"])
llm_service = LLMService()


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest):
    try:
        intent = llm_service.parse_intent(request.message)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Local LLM is unavailable or returned an invalid response: {exc}",
        ) from exc

    # External APIs, DB, and recommendation logic will be connected here later.
    return ChatResponse(
        message="Intent parsing completed.",
        intent=intent,
        places=[],
    )
