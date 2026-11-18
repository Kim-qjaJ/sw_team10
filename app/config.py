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
    # 기본 LLM(groq)이 한도 초과·장애일 때 넘길 LLM: ollama | none. 한도 초과 뒤 이 시간 동안은 바로 대체 LLM을 쓴다.
    llm_fallback_provider: str = "ollama"
    llm_fallback_cooldown_seconds: float = 60.0

    # Local Ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_timeout_seconds: float = 60.0
    ollama_keep_alive: str = "30m"
    ollama_structured_output: bool = True
    # 생각(thinking) 단계를 끈다. 켜면 맥미니에서 해석 한 번에 25~35초, 끄면 약 8초 (2026-11-16 측정)
    ollama_think: bool = False

    # GroqCloud
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: float = 30.0
    groq_structured_output: bool = True

    use_mock_places: bool = True
    provider_timeout_seconds: float = 5.0
    # Provider 하나를 기다리는 최대 시간 (SRS NFR-001: 일부 API가 늦어도 6초 안에 받은 결과로 응답).
    # 넘기면 그 Provider 없이 응답하고, 하던 다운로드는 뒤에서 마저 받아 다음 요청부터 캐시를 쓴다.
    provider_wait_seconds: float = 5.5

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
    # 전시: 한국문화정보원 한눈에보는문화정보조회서비스 (공공데이터포털 키 공용)
    culture_info_url: str = "https://apis.data.go.kr/B553457/cultureinfo"

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
