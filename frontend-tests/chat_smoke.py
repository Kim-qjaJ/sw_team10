"""메신저형 기본 화면(/) 브라우저 시험.

실행: .venv/bin/python frontend-tests/chat_smoke.py (8000 포트 Mock 서버 필요: USE_MOCK_PLACES=true)
OSM 타일 요청은 막아 서비스에 부하를 주지 않습니다. 오류 응답은 이 파일 안에서만 흉내 냅니다.
"""
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
API = '**/api/v1/recommend'


def ask(page, text):
    page.locator('#message').fill(text)
    with page.expect_response(API):
        page.locator('#message').press('Enter')
    expect(page.locator('.typing-bubble')).to_have_count(0, timeout=50000)


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '가로 넘침'


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    errors = []

    # PC: 결과 카드·번호·지도 펼치기·근처 다시 찾기·새 대화
    pc = browser.new_context(viewport={'width': 1440, 'height': 900})
    pc.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
    page = pc.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE)
    expect(page.locator('#start-credit')).to_contain_text('틀릴 수 있어요')
    expect(page.locator('#send')).to_be_disabled()
    page.locator('#message').fill('서면')
    page.locator('#message').press('Shift+Enter')
    assert '\n' in page.locator('#message').input_value()
    ask(page, '서면 맛집이랑 카페 추천해줘')
    expect(page.locator('#start-credit')).to_have_count(0)
    cards = page.locator('.place-card')
    assert cards.count() >= 2
    # 화면의 추천 점수는 묶음 안에서 위에서 아래로 내려가야 한다 (점수와 순위 기준이 같음)
    for track in page.locator('.carousel').all():
        shown = [float(t.split()[2]) for t in track.locator('.score').all_inner_texts()]
        assert shown == sorted(shown, reverse=True), shown
    numbers = [int(n) for n in page.locator('.place-card .num').all_inner_texts()]
    assert numbers == list(range(1, len(numbers) + 1)), '카드 번호는 묶음을 이어서 1부터'
    expect(page.locator('#quick-replies button').first).to_be_visible()
    page.locator('#map-expand').click()
    expect(page.locator('#map-panel')).to_have_class('map-panel expanded')
    page.keyboard.press('Escape')
    expect(page.locator('#map-panel')).not_to_have_class('map-panel expanded')
    near = page.locator('.near-btn').first
    with page.expect_request(API) as req:
        near.click()
    assert '근처' in req.value.post_data_json['message']
    expect(page.locator('.typing-bubble')).to_have_count(0, timeout=50000)
    assert page.locator('.msg.bot').count() >= 3
    page.locator('#map-expand').click()
    page.locator('#new-chat').click()
    expect(page.locator('#map-panel')).not_to_have_class('map-panel expanded')
    expect(page.locator('.place-card')).to_have_count(0)
    expect(page.locator('#start-credit')).to_be_visible()

    # 오류: 서버 500 → 안내와 '다시 시도'
    page.route(API, lambda route: route.fulfill(status=500, body='{}', content_type='application/json'))
    page.locator('#message').fill('해운대 맛집')
    page.locator('#message').press('Enter')
    expect(page.locator('.retry-btn')).to_be_visible()
    page.unroute(API)
    with page.expect_response(API):
        page.locator('.retry-btn').click()
    expect(page.locator('.typing-bubble')).to_have_count(0, timeout=50000)
    print('PASS PC: 입력·점수 순서·번호·지도 펼치기/Esc·근처 다시 찾기·새 대화·오류와 다시 시도')

    # 모바일·태블릿: 가로 넘침 없음, 아래 지도 창, 닫힌 지도는 화면 밖, 긴 답은 첫 줄부터
    for width, height in [(319, 700), (375, 812), (768, 1024), (1024, 768)]:
        ctx = browser.new_context(viewport={'width': width, 'height': height}, has_touch=width < 768)
        ctx.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
        m = ctx.new_page()
        m.on('pageerror', lambda e: errors.append(str(e)))
        m.goto(BASE)
        no_overflow(m)
        assert m.evaluate("[...document.querySelectorAll('#composer select, #toggle-map, #new-chat')]"
                          ".every(e => e.getBoundingClientRect().right <= innerWidth && e.getBoundingClientRect().height < 40)"), \
            f'{width}px: 선택기·상단 버튼이 잘리거나 줄바꿈됨'
        assert m.evaluate("document.querySelector('#map-panel').getBoundingClientRect().top >= innerHeight"), '닫힌 지도가 비침'
        ask(m, '서면 맛집이랑 카페 추천해줘')
        no_overflow(m)
        top = m.evaluate("""() => { const log = document.querySelector('#chat-log');
            const bots = document.querySelectorAll('.msg.bot'); const msg = bots[bots.length - 1];
            return msg.getBoundingClientRect().top - log.getBoundingClientRect().top; }""")
        assert top >= -1, f'{width}px: 답의 첫 줄이 위로 가려짐 ({top})'
        m.locator('#toggle-map').click()
        expect(m.locator('#map-panel')).to_have_class('map-panel open')
        # 지도 창이 열리면 초점이 '닫기'로 가고, 뒤 대화 화면으로 Tab이 빠지지 않으며, 닫으면 연 버튼으로 돌아온다
        expect(m.locator('#close-map')).to_be_focused()
        for _ in range(4):
            m.keyboard.press('Tab')
            assert m.evaluate("!document.activeElement.closest('.room')"), '지도 창 뒤로 초점이 나감'
        m.keyboard.press('Escape')
        expect(m.locator('#map-panel')).not_to_have_class('map-panel open')
        expect(m.locator('#toggle-map')).to_be_focused()
        ctx.close()
    print('PASS 319/375/768/1024px: 가로 넘침·잘림 없음, 지도 창 초점 이동·복귀, 닫힌 지도 숨김, 긴 답 첫 줄 표시')

    assert not errors, errors
    print('PASS JS 오류 없음')
    browser.close()
