from pathlib import Path
from urllib.parse import unquote

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


class Settings(BaseSettings):
    # 실행 위치와 상관없이 프로젝트 루트의 .env를 읽는다.
    model_config = SettingsConfigDict(env_file=PROJECT_DIR / ".env", extra="ignore")

    # LLM Provider 선택: ollama | groq
    llm_provider: str = "groq"

    # Local Ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_timeout_seconds: float = 60.0
    ollama_keep_alive: str = "30m"
    ollama_structured_output: bool = True

    # GroqCloud
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: float = 30.0
    groq_structured_output: bool = True

    use_mock_places: bool = True
    provider_timeout_seconds: float = 5.0

    # 응답 시간 상한 (SRS NFR-001)
    # - 요청 전체: LLM 해석·결과 요약을 포함해 이 시간 안에 결과 또는 안내를 돌려준다.
    # - LLM 해석: 이 시간을 넘기면 LLM을 기다리지 않고, 사전으로 확인된 조건이 있으면 기본 검색을 한다.
    request_timeout_seconds: float = 30.0
    llm_intent_timeout_seconds: float = 20.0
    # 주변 시설 찾기: 현재 위치가 부산 밖이면 검색하지 않고 기준 장소를 묻는다.
    # 부산 밖에서 개발/시연할 때는 false로 두면 현재 위치 그대로 검색한다.
    nearby_busan_only: bool = True

    # 지도/장소 검색 API
    kakao_rest_api_key: str | None = None
    naver_client_id: str | None = None
    naver_client_secret: str | None = None

    # 공공데이터포털 일반 인증키.
    # TourAPI와 기상청 API가 이 키를 공통으로 사용한다.
    public_data_api_key: str | None = None

    # 실제 활용 신청한 공공데이터 API의 요청 URL.
    tour_api_url: str | None = None
    kma_api_url: str | None = None

    # 부산광역시 공공데이터 (부산 특화 데이터). 공공데이터포털에서 각각 활용 신청한다.
    # 관광 요청: 부산명소정보, 전시 요청: 전시 목록
    busan_attraction_url: str = "https://apis.data.go.kr/6260000/AttractionService/getAttractionKr"
    busan_exhibit_url: str = "https://apis.data.go.kr/6260000/BusanCultureExhibitService/getBusanCultureExhibit"

    # Supabase 질의 로그 (SRS NFR-003, DATA-09 잠정안)
    # QUERY_LOG_ENABLED=true이고 URL·키가 모두 있을 때만 저장한다. 키는 서버 전용 secret 키를 쓴다.
    query_log_enabled: bool = False
    supabase_url: str | None = None
    supabase_secret_key: str | None = None
    supabase_timeout_seconds: float = 3.0

    @staticmethod
    def _decode_service_key(value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip()
        return unquote(value) if value else None

    @property
    def public_data_service_key(self) -> str | None:
        """공공데이터포털 Encoding/Decoding 키 어느 쪽도 입력 가능하게 정규화한다."""
        return self._decode_service_key(self.public_data_api_key)


settings = Settings()
