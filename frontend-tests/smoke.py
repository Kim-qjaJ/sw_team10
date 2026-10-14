"""실행: .venv/bin/python frontend-tests/smoke.py (8000 포트 Mock 서버 필요).
실제 API 연결 + 오류 주입 브라우저 테스트. OSM 타일 요청은 차단해 서비스에 부하를 주지 않습니다.
테스트용 응답/위치는 이 파일에만 존재하며 운영 화면에 포함되지 않습니다.
"""
import base64
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8000'
API='**/api/v1/recommend'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')

def submit(page, text):
    page.locator('#message').fill(text)
    page.locator('#submit').click()
    expect(page.locator('#submit')).to_be_enabled(timeout=50000)

with sync_playwright() as pw:
    browser=pw.chromium.launch()
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.route('https://tile.openstreetmap.org/**',lambda route:route.abort())
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE)
    expect(page.locator('#map-status')).to_contain_text('불러오지 못했어요')
    submit(page,'   ')
    expect(page.locator('#error')).to_be_visible()
    expect(page.locator('#retry')).to_be_hidden()
    page.locator('#message').fill('서면')
    page.locator('#message').press('Shift+Enter')
    assert '\n' in page.locator('#message').input_value()
    page.locator('#message').fill('서면 카페 추천해줘')
    with page.expect_response(API) as response:
        page.locator('#message').press('Enter')
    data=response.value.json()
    expect(page.locator('.place-card')).to_have_count(sum(len(g['places']) for g in data['results']))
    expect(page.locator('#mock-label')).to_be_visible()
    assert '추천 점수' not in page.locator('#results').inner_text()
    sid=page.evaluate("sessionStorage.getItem('busan-session')")
    page.locator('.place-card button').first.click()
    expect(page.locator('.leaflet-popup').last).to_be_visible()
    expect(page.locator('.place-card.selected')).to_have_count(1)
    page.locator('.leaflet-interactive').last.focus()
    page.locator('.leaflet-interactive').last.press('Enter')
    expect(page.locator('.leaflet-popup').last).to_be_visible()
    with page.expect_request(API) as req:
        page.locator('#suggestions button').first.click()
    assert req.value.post_data_json['session_id']==sid
    expect(page.locator('#submit')).to_be_enabled(timeout=50000)
    submit(page,'해운대 맛집 알려줘')
    expect(page.locator('.place-card')).to_have_count(4)
    page.screenshot(path='/tmp/frontend-desktop.png',full_page=True)
    for mode,message in [('search','서면 카페'),('nearby','서면역 약국')]:
        page.locator(f'input[value={mode}]').check()
        submit(page,message)
        assert page.locator('.place-card').count()>0
    page.reload()
    assert page.evaluate("sessionStorage.getItem('busan-session')")==sid
    page.locator('summary').first.click()
    page.locator('#image-input').set_input_files({'name':'photo.png','mimeType':'image/png','buffer':PNG})
    expect(page.locator('#image-preview-wrap')).to_be_visible()
    expect(page.locator('#image-status')).to_contain_text('전송되지 않았어요')
    page.locator('#remove-image').click()
    expect(page.locator('#image-preview-wrap')).to_be_hidden()
    for name,mime,content,expected in [('test.txt','text/plain',b'abc','이미지 파일만'),('large.png','image/png',b'0'*(5*1024*1024+1),'5MB 이하'),('broken.png','image/png',b'broken','읽을 수 없어요')]:
        page.locator('#image-input').set_input_files({'name':name,'mimeType':mime,'buffer':content})
        expect(page.locator('#image-status')).to_contain_text(expected)
    print('PASS 실제 API 4개 질의, Enter/줄바꿈, 세션, 후속 질문, 카드/마커, 이미지')

    # 테스트 응답은 반드시 브라우저 네트워크 계층에서만 주입합니다.
    fixture={'intent':{'intent':'recommend_place'},'results':[{'request':{'query':'<img src=x onerror=alert(1)>'},'result_type':'recommend','places':[{'name':'<img src=x onerror=alert(1)>','url':'javascript:alert(1)','latitude':None,'longitude':None,'score':0}]},{'request':{'query':'두 번째 그룹'},'result_type':'search','places':[]}], 'mock_data':True,'timing_ms':{},'suggestions':[{'label':'검색 모드','message':'','mode':'search'}],'links':[{'label':'위험 링크','url':'data:text/html,hi'}]}
    def respond(route): route.fulfill(status=200,content_type='application/json',body=json.dumps(fixture))
    page.route(API,respond)
    submit(page,'테스트')
    expect(page.locator('.group')).to_have_count(2)
    expect(page.locator('#results img')).to_have_count(0)
    expect(page.locator('#results a')).to_have_count(0)
    expect(page.locator('#results')).to_contain_text('좌표 정보 없음')
    page.locator('#suggestions button').click()
    expect(page.locator('input[value=search]')).to_be_checked()
    page.unroute(API,respond)
    for status,body,expected in [(429,'{}','요청이 많아'),(500,'{}','서버가 요청'),(200,'not json','JSON'),(200,'{}','형식')]:
        page.route(API,lambda route,request,s=status,b=body:route.fulfill(status=s,body=b,content_type='application/json'))
        submit(page,'테스트')
        expect(page.locator('#error-text')).to_contain_text(expected)
        page.unroute(API)
    page.route(API,lambda route:route.abort())
    submit(page,'테스트')
    expect(page.locator('#error-text')).to_contain_text('연결할 수 없어요')
    page.unroute(API)
    fixture['results']=[]
    page.route(API,respond)
    submit(page,'테스트')
    expect(page.locator('#results')).to_contain_text('표시할 장소가 없어요')
    page.unroute(API)
    # 응답을 멈추고 가상 시간으로 45초 타임아웃과 중복 제출 방지 검증.
    page.clock.install()
    held=[]
    page.route(API,lambda route:held.append(route))
    page.locator('#message').fill('서면 카페')
    page.locator('#submit').click()
    expect(page.locator('#submit')).to_be_disabled()
    page.locator('#message').press('Enter')
    page.wait_for_timeout(100)
    assert len(held)==1
    page.clock.fast_forward(46000)
    expect(page.locator('#error-text')).to_contain_text('45초')
    for route in held: route.abort()
    page.unroute(API)
    print('PASS 다중 그룹, XSS/위험 URL 차단, 좌표 없음, 429/500/JSON/연결 실패/0건/타임아웃/중복 제출')

    # 실제 OS 권한창을 대신하는 결정적 브라우저 Geolocation 테스트.
    for code,expected in [(1,'권한이 거부'),(3,'시간이 초과')]:
        page.evaluate(f"() => {{ navigator.geolocation.getCurrentPosition=(ok,fail)=>fail({{code:{code}}}); }}")
        page.locator('#locate').click()
        expect(page.locator('#location-status')).to_contain_text(expected)
    page.evaluate('() => { navigator.geolocation.getCurrentPosition=ok=>ok({coords:{latitude:35.16,longitude:129.06}}); }')
    page.locator('#locate').click()
    expect(page.locator('#location-status')).to_contain_text('확인했어요')
    page.route(API,respond)
    with page.expect_request(API) as req: submit(page,'테스트')
    assert req.value.post_data_json['latitude']==35.16
    page.locator('#clear-location').click()
    with page.expect_request(API) as req: submit(page,'테스트')
    assert 'latitude' not in req.value.post_data_json
    assert req.value.post_data_json['session_id']!=sid
    page.unroute(API)
    for width in [390,320]:
        page.set_viewport_size({'width':width,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path='/tmp/frontend-mobile.png',full_page=True)
    assert not errors, errors
    print('PASS GPS 성공/거부/타임아웃/해제, 모바일 390/320px 가로 넘침 없음, JS 오류 없음')
    page.close()
    no_map=context.new_page()
    no_map.route('**/vendor/leaflet/leaflet.js',lambda route:route.abort())
    no_map.goto(BASE)
    expect(no_map.locator('#map-status')).to_contain_text('지도 프로그램')
    no_map.locator('#category').select_option('cafe')
    with no_map.expect_request(API) as req: submit(no_map,'서면 카페 추천해줘')
    assert req.value.post_data_json['category']=='cafe'
    assert no_map.locator('.place-card').count()>0
    print('PASS 지도 SDK 로드 실패 시 검색 유지, 카테고리 API 전달')
    browser.close()
