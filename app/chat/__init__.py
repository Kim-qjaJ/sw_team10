"""채팅 요청 처리 계층.

main.py(엔드포인트) → service.handle_chat → 모드별 핸들러(search / nearby / followup / conversation)
→ RecommendationPipeline 순서로 호출된다. 핸들러는 FastAPI의 app.state를 ctx로 받아
pipeline, intent_parser, response_writer, sessions(SessionStore)를 사용한다.
"""
