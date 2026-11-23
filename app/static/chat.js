import { el, finite, validCoords, link, safeURL, validateResponse, categoryNames } from "./ui-utils.js?v=20261117";
import { createChatMap } from "./chat-map.js?v=20261117g";

const SERVICE_NAME = "부산 한걸음";
const BOT_NAME = "부기";
// 부기 프로필 사진. 이 경로에 이미지가 있으면 동그란 프로필로, 없으면 기본 원형 표시를 씁니다.
const AVATAR_URL = "/static/images/boogi.png?v=20261117";
const REQUEST_TIMEOUT_MS = 45000;
const DATA_CREDIT =
  "데이터: 카카오·네이버 지역검색, 한국관광공사(TourAPI·사진), 부산광역시 공공데이터(명소·사진), 한국문화정보원(전시), 기상청, TMAP(경로 거리) · " +
  "행정동 경계 © vuski/admdongkor (CC BY 4.0) · 지도 © OpenStreetMap 기여자";
// 1100px 이상: 오른쪽 위 작은 지도(누르면 크게). 그보다 좁으면 아래에서 올라오는 지도.
const WIDE = window.matchMedia("(min-width: 1100px)");
const $ = (selector) => document.querySelector(selector);
const chatLog = $("#chat-log");
const input = $("#message");

document.querySelectorAll("[data-service-name]").forEach((n) => (n.textContent = SERVICE_NAME));

const probe = new Image();
probe.onload = () => document.body.classList.add("has-avatar");
probe.src = AVATAR_URL;

let busy = false,
  coords = null,
  gpsVersion = 0,
  sessionId = newId(),
  lastRequest = null,
  responseCount = 0;

function newId() {
  return crypto.randomUUID ? crypto.randomUUID() : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const chatMap = createChatMap();

/* ---------- 검색 방식 ---------- */
const mode = () => $("input[name=mode]:checked").value;
const PLACEHOLDERS = {
  recommend: "예: 서면에서 친구랑 갈 조용한 카페",
  search: "예: 부산역, 이재모피자",
  nearby: "예: 약국 (현재 위치를 켜거나 '서면역 약국'처럼)",
};
// 좁은 휴대폰에서는 입력칸 한 줄에 들어가는 짧은 예시를 보여준다 (긴 예시는 두 줄로 잘려 보임).
const SHORT_PLACEHOLDERS = { recommend: "예: 서면 조용한 카페", search: "예: 부산역, 이재모피자", nearby: "예: 약국, 서면역 약국" };
const NARROW = window.matchMedia("(max-width: 400px)");
NARROW.addEventListener("change", () => updateMode());
function updateMode() {
  $("#category-wrap").hidden = mode() !== "recommend";
  input.placeholder = (NARROW.matches ? SHORT_PLACEHOLDERS : PLACEHOLDERS)[mode()];
}
function setMode(value) {
  const radio = [...document.querySelectorAll("[name=mode]")].find((n) => n.value === value);
  if (radio) {
    radio.checked = true;
    updateMode();
  }
}
document.querySelectorAll("[name=mode]").forEach((n) => n.addEventListener("change", updateMode));
updateMode();

/* ---------- 메시지 그리기 ---------- */
const timeFormat = new Intl.DateTimeFormat("ko-KR", { hour: "numeric", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
const now = () => timeFormat.format(new Date());

let scrollSeq = 0; // 나중에 정한 스크롤이 앞서 예약된 '맨 아래로'에 덮이지 않게 하는 번호
function scrollDown() {
  const seq = ++scrollSeq;
  requestAnimationFrame(() => seq === scrollSeq && chatLog.scrollTo({ top: chatLog.scrollHeight, behavior: "auto" }));
}
// 답이 화면보다 길면(결과 묶음이 여러 개인 모바일 등) 맨 아래 대신 답의 첫 줄을 보여 줘서
// 요약 문장과 안내(직선거리 추정 등)를 놓치지 않게 합니다.
function scrollToReply(msg) {
  // 크기를 바로 재서(requestAnimationFrame 없이) 정한다. 그래야 뒤늦게 뜬 사진의 '맨 아래로'가 이 결정을 덮어쓰지 않는다.
  if (msg.offsetHeight > chatLog.clientHeight - 24) {
    scrollSeq++;
    chatLog.scrollTo({ top: chatLog.scrollTop + msg.getBoundingClientRect().top - chatLog.getBoundingClientRect().top - 8, behavior: "auto" });
    pinned = false; // 뒤늦게 뜨는 사진 때문에 다시 맨 아래로 내려가지 않게
  } else scrollDown();
}
// 사진이 늦게 뜨면 높이가 바뀌므로, 맨 아래를 보고 있었을 때만 다시 내려 줍니다.
let pinned = true;
chatLog.addEventListener("scroll", () => {
  pinned = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
});
function addCenter(text, className) {
  const row = el("div", null, className);
  row.append(el("span", text));
  chatLog.append(row);
  scrollDown();
}
function addUser(text) {
  const msg = el("div", null, "msg me");
  const row = el("div", null, "bubble-row");
  row.append(el("time", now()), el("div", text, "bubble"));
  msg.append(row);
  chatLog.append(msg);
  scrollDown();
}
function botMessage() {
  const msg = el("div", null, "msg bot");
  const avatar = el("span", null, "avatar");
  avatar.setAttribute("aria-hidden", "true");
  const body = el("div", null, "msg-body");
  body.append(el("div", BOT_NAME, "sender"));
  msg.append(avatar, body);
  chatLog.append(msg);
  return { msg, body };
}
function addBubble(body, content, className) {
  const row = el("div", null, "bubble-row");
  const bubble = el("div", null, `bubble${className ? ` ${className}` : ""}`);
  if (typeof content === "string") bubble.textContent = content;
  else bubble.append(...content);
  row.append(bubble);
  body.append(row);
  return bubble;
}
// 카카오톡처럼 마지막 말풍선 옆에만 시간을 붙입니다.
function stamp(body) {
  const rows = body.querySelectorAll(":scope > .bubble-row");
  if (rows.length) rows[rows.length - 1].append(el("time", now()));
}
function showTyping() {
  const { msg, body } = botMessage();
  const bubble = addBubble(body, [el("span", null, "dot"), el("span", null, "dot"), el("span", null, "dot")], "typing-bubble");
  bubble.setAttribute("role", "img");
  bubble.setAttribute("aria-label", `${BOT_NAME}가 장소를 찾고 있어요`);
  scrollDown();
  return msg;
}

/* ---------- 빠른 답장 ---------- */
const STARTERS = [
  { label: "서면 카페 추천", message: "서면 카페 추천해줘", mode: "recommend" },
  { label: "해운대 맛집", message: "해운대 맛집 알려줘", mode: "recommend" },
  { label: "기장 관광지", message: "기장 관광지 추천해줘", mode: "recommend" },
  { label: "비 오는 날 광안리", message: "비 오는 날 광안리 갈 만한 곳", mode: "recommend" },
  { label: "부산역 찾기", message: "부산역", mode: "search" },
];
function setQuickReplies(items) {
  const bar = $("#quick-replies");
  bar.replaceChildren();
  items.forEach((item) => {
    const b = el("button", item.label);
    b.type = "button";
    b.disabled = busy;
    b.addEventListener("click", () => {
      if (busy) return;
      if (item.mode) setMode(item.mode);
      if (item.message.trim()) send(item.message);
      else input.focus();
    });
    bar.append(b);
  });
  bar.hidden = !items.length;
}

/* ---------- 보내기 ---------- */
function setBusy(value) {
  busy = value;
  $("#composer").setAttribute("aria-busy", String(value));
  document.querySelectorAll("#quick-replies button, .retry-btn").forEach((n) => (n.disabled = value));
  document.querySelectorAll(".near-btn:not([title])").forEach((n) => (n.disabled = value));
  updateSend();
}
function updateSend() {
  $("#send").disabled = busy || !input.value.trim();
}
function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}
input.addEventListener("input", () => {
  resizeInput();
  updateSend();
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send(input.value);
});

function payloadFor(text) {
  const payload = { message: text, limit: Number($("#limit").value), session_id: sessionId, mode: mode() };
  if (mode() === "recommend" && $("#category").value) payload.category = $("#category").value;
  if (coords) Object.assign(payload, coords);
  return payload;
}
// shown: 말풍선에 보일 문장 (버튼은 '3번 근처 맛집' 대신 '브룩스 근처 맛집'처럼 보여준다)
function send(raw, shown) {
  const text = raw.trim();
  if (busy || !text) return;
  if ([...text].length > 500) {
    addCenter("질문은 500자까지 보낼 수 있어요.", "system-msg");
    return;
  }
  document.querySelector("#start-credit")?.remove();
  addUser(shown || text);
  if (!shown) {
    input.value = "";
    resizeInput();
  }
  request(payloadFor(text));
}
async function request(payload) {
  lastRequest = payload;
  setBusy(true);
  setQuickReplies([]);
  const typing = showTyping();
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/api/v1/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) {
      // 서버가 원인별 안내(LLM 한도 초과 등)를 보내면 그 문장을 그대로 보여줍니다.
      let detail = null;
      try {
        const body = await response.json();
        if (typeof body?.detail?.message === "string") detail = body.detail.message;
      } catch {
        /* 본문이 JSON이 아니면 기본 안내 사용 */
      }
      if (detail) throw new Error(detail);
      throw new Error(
        response.status === 429
          ? "요청이 많아 잠시 쉬고 있어요. 조금 뒤에 다시 물어봐 주세요."
          : response.status === 422
            ? "입력 조건을 확인해 주세요. 질문은 500자 이하로 보내야 해요."
            : `서버가 요청을 처리하지 못했어요 (${response.status}). 잠시 후 다시 시도해 주세요.`,
      );
    }
    let data;
    try {
      data = await response.json();
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error("서버에서 올바른 응답을 받지 못했어요. 다시 시도해 주세요.");
    }
    typing.remove();
    renderResponse(validateResponse(data));
  } catch (e) {
    typing.remove();
    renderError(
      e.name === "AbortError"
        ? "45초 동안 답을 받지 못했어요. 잠시 후 다시 시도해 주세요."
        : e instanceof TypeError
          ? "서버에 연결할 수 없어요. 인터넷 연결과 서버 상태를 확인해 주세요."
          : e.message,
    );
  } finally {
    clearTimeout(timer);
    setBusy(false);
    input.focus({ preventScroll: true });
  }
}
function renderError(message) {
  const { body } = botMessage();
  const retry = el("button", "다시 시도", "bubble-btn retry-btn");
  retry.type = "button";
  retry.addEventListener("click", () => {
    if (!busy && lastRequest) request(lastRequest);
  });
  const actions = el("div", null, "bubble-actions");
  actions.append(retry);
  addBubble(body, [el("p", message), actions], "error");
  stamp(body);
  scrollDown();
}

/* ---------- 응답 그리기 ---------- */
const RESULT_NAMES = { exact: "특정 장소", brand: "브랜드 검색", search: "검색 결과", recommend: "추천" };
const SCORE_NAMES = {
  category: "종류",
  access: "접근성",
  condition: "사용자 조건",
  weather: "날씨",
  cross: "여러 출처 확인",
  busan: "부산 특화",
};
// 출처는 내부 이름(kakao 등) 대신 사람이 알아보는 이름으로 보여준다.
const SOURCE_NAMES = { kakao: "카카오맵", naver: "네이버", tour: "한국관광공사", busan: "부산 공공데이터" };
const sourceName = (source, p) =>
  source === "busan" && Array.isArray(p.events) && p.events.length ? "한국문화정보원" : SOURCE_NAMES[source] || source;
const hasScore = (p) =>
  p.score_breakdown &&
  typeof p.score_breakdown === "object" &&
  Object.values(p.score_breakdown).some((v) => v && v.available === true && finite(v.score));

function renderResponse(data) {
  const id = ++responseCount;
  const { msg, body } = botMessage();
  const head = [];
  if (data.mock_data) head.push(el("span", "샘플 데이터", "tag sample"));
  const notices = [data.notice];
  const mainText = data.message || data.intent.question || (data.notice ? null : "결과를 확인해 주세요.");
  if (mainText) head.push(el("p", mainText));
  const recommended = data.results.filter((r) => r.result_type === "recommend").flatMap((r) => r.places);
  if (recommended.length && !recommended.some(hasScore))
    notices.push("이번 결과에는 계산된 점수 근거가 없어요. 순서를 평가 순위로 보지 말아 주세요.");
  notices.filter(Boolean).forEach((n) => head.push(el("p", n, "notice")));
  // 검색 기준은 장소를 보여줄 때만 붙입니다 (인사·상세 답변에는 생략).
  const found = data.results.some((r) => r.places.length);
  const meta = found
    ? [data.intent.location, finite(data.radius_m) ? `반경 ${data.radius_m.toLocaleString()}m` : null].filter(Boolean)
    : [];
  if (meta.length) head.push(el("p", `거리 기준 ${meta.join(" · ")}`, "meta"));
  cardAnchor = (data.intent.location || "").replace(/^부산\s*/, "").replace(/\s*근처$/, "").trim();
  addBubble(body, head);

  const entries = [];
  let number = 0;
  // 서버는 가장 최근 결과의 번호만 기억하므로, 새 결과가 오면 이전 카드의 '근처' 버튼은 끈다.
  if (data.results.some((r) => r.places.length)) retireNearButtons("최근 결과에서만 쓸 수 있어요");
  data.results.forEach((result, groupIndex) => {
    if (!result.places.length) {
      addBubble(body, `‘${result.request.query}’ 조건에 맞는 장소는 찾지 못했어요. 지역이나 종류를 바꿔 볼까요?`);
      return;
    }
    const block = el("div", null, "result-block");
    block.append(
      el("p", `${result.request.query} · ${RESULT_NAMES[result.result_type] || "장소"} ${result.places.length}곳`, "result-label"),
    );
    const track = el("div", null, "carousel");
    track.setAttribute("role", "list");
    track.setAttribute("aria-label", `${result.request.query} 결과`);
    result.places.forEach((place, index) => {
      number++;
      const key = `${id}-${groupIndex}-${index}`;
      const card = makeCard(place, key, number, result.result_type, () => entries, data.mock_data);
      track.append(card);
      entries.push({ place, key, card, number });
    });
    block.append(withArrows(track));
    body.append(block);
  });

  const links = (data.links || []).map((item) => link(item.label, item.url)).filter(Boolean);
  if (links.length) {
    const actions = el("div", null, "bubble-actions");
    actions.append(...links);
    addBubble(body, [el("p", "바로가기"), actions]);
  }
  stamp(body);
  if (entries.length) chatMap.update(entries, data.mock_data);
  setQuickReplies(data.suggestions || []);
  scrollToReply(msg);
}

// 마우스로는 옆으로 넘기기 어려우므로 좌우 화살표를 붙인다 (터치 화면에서는 CSS로 숨김).
function withArrows(track) {
  const wrap = el("div", null, "carousel-wrap");
  const prev = el("button", "‹", "carousel-nav prev");
  const next = el("button", "›", "carousel-nav next");
  prev.type = next.type = "button";
  prev.setAttribute("aria-label", "이전 장소");
  next.setAttribute("aria-label", "다음 장소");
  const step = () => (track.querySelector(".place-card")?.offsetWidth || 236) + 8;
  prev.addEventListener("click", () => track.scrollBy({ left: -step(), behavior: "smooth" }));
  next.addEventListener("click", () => track.scrollBy({ left: step(), behavior: "smooth" }));
  const update = () => {
    prev.hidden = track.scrollLeft <= 2;
    next.hidden = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
  };
  track.addEventListener("scroll", update, { passive: true });
  // 카드가 화면에 놓이거나 크기가 바뀔 때(사진 로드, 창 크기 변경) 화살표 상태를 다시 계산한다.
  if (window.ResizeObserver) new ResizeObserver(update).observe(track);
  else window.addEventListener("resize", update);
  setTimeout(update, 0);
  wrap.append(prev, track, next);
  return wrap;
}

function nearTargets(category) {
  if (category === "restaurant") return ["카페", "관광지"];
  if (category === "cafe") return ["맛집", "관광지"];
  return ["맛집", "카페"];
}

let cardAnchor = ""; // 지금 그리는 결과의 기준점 이름 (거리 0m 표시용)
function makeCard(p, key, number, resultType, getEntries, mock) {
  const card = el("article", null, "place-card");
  card.setAttribute("role", "listitem");
  // 공공 API 대표 사진. https로 바꿔 불러오고, 실패하면 사진만 뺍니다.
  const photo = safeURL(typeof p.image_url === "string" ? p.image_url.replace(/^http:/, "https:") : null);
  if (photo) {
    const img = el("img", null, "card-photo");
    img.src = photo;
    img.alt = `${p.name} 사진`;
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => img.remove());
    img.addEventListener("load", () => pinned && scrollDown());
    card.append(img);
  }
  const inner = el("div", null, "card-body");
  const head = el("div", null, "card-head");
  const isFestival = Array.isArray(p.events) && p.events.some((e) => e && e.kind === "festival");
  head.append(el("span", String(number), "num"),
    el("span", isFestival ? "축제·행사" : categoryNames[p.category] || p.category || "분류 없음", "tag"));
  inner.append(head, el("h3", p.name), el("p", p.address || "주소 정보 없음"));
  if (p.phone) inner.append(el("p", `전화 ${p.phone}`));
  if (typeof p.rest_info === "string" && p.rest_info) inner.append(el("p", `쉬는 날 ${p.rest_info}`, "rest"));
  else if (p.subcategory === "exhibition")
    inner.append(el("p", "쉬는 날 정보 없음 · 방문 전 운영 여부를 확인해 주세요", "rest"));

  if (Array.isArray(p.events) && p.events.length) {
    const SHOWN = 3;
    const valid = p.events.filter((e) => e && typeof e.title === "string");
    const list = el("ul", null, "events");
    for (const e of valid.slice(0, SHOWN)) {
      const period =
        e.open_run && typeof e.end !== "string" ? "상시" : [e.start, e.end].filter((d) => typeof d === "string").join(" ~ ");
      const extra = [period, e.free ? "무료" : null].filter(Boolean).join(", ");
      // 축제 카드는 제목이 장소 이름과 같으므로 기간만 보여준다.
      if (e.kind === "festival" && e.title === p.name) list.append(el("li", extra || "기간 정보 없음"));
      else list.append(el("li", extra ? `${e.title} (${extra})` : e.title));
    }
    const festival = valid.some((e) => e.kind === "festival");
    if (valid.length > SHOWN) list.append(el("li", `외 ${valid.length - SHOWN}개 ${festival ? "행사" : "전시"}`));
    if (list.childElementCount) inner.append(el("p", festival ? "진행 기간" : "진행 중인 전시"), list);
  }

  const metrics = [];
  if (finite(p.distance_m) && p.distance_m >= 0) {
    const text = p.distance_m >= 1000 ? `${(p.distance_m / 1000).toFixed(1)}km` : `${Math.round(p.distance_m)}m`;
    // TMAP 길 거리를 받지 못한 장소는 직선거리임을 밝힌다.
    // 기준점 50m 안: 기준 장소 자체(해운대 검색의 해운대해수욕장)는 '검색 기준점', 그 옆 가게는 '검색 기준점 인근'.
    if (p.distance_m < 50 && p.distance_source !== "tmap") {
      const norm = (s) => String(s || "").replace(/[^0-9a-zA-Z가-힣]/g, "");
      const own = cardAnchor && (norm(p.name).includes(norm(cardAnchor)) || norm(cardAnchor).includes(norm(p.name)));
      const where = cardAnchor ? `${cardAnchor} ` : "";
      metrics.push(own ? `${where}검색 기준점` : `${where}검색 기준점 인근 (${Math.round(p.distance_m)}m)`);
    } else metrics.push(p.distance_source === "straight" ? `직선 ${text}` : text);
  }
  [
    ["walking_minutes", "도보"],
    ["transit_minutes", "대중교통"],
    ["driving_minutes", "자동차"],
  ].forEach(([k, label]) => {
    if (finite(p[k]) && p[k] >= 0) metrics.push(`${label} ${Math.round(p[k])}분`);
  });
  // '걸어서 5분 이내'를 경로 시간 없이 직선거리로 판단한 곳은 추정이라고 밝힌다.
  if (finite(p.estimated_travel_minutes) && !["walking_minutes", "transit_minutes", "driving_minutes"].some((k) => finite(p[k])))
    metrics.push(`약 ${Math.max(1, Math.round(p.estimated_travel_minutes))}분 (직선거리 추정)`);
  if (metrics.length) inner.append(el("p", metrics.join(" · "), "metrics"));

  const breakdown =
    p.score_breakdown && typeof p.score_breakdown === "object"
      ? Object.entries(p.score_breakdown).filter(([, v]) => v && typeof v === "object")
      : [];
  if (resultType === "recommend" && finite(p.score) && hasScore(p)) {
    const score = el("p", `추천 점수 ${p.score.toFixed(1)}`, "score");
    score.append(el("small", " / 100"));
    inner.append(score);
  }
  if (breakdown.length) {
    const details = el("details");
    details.append(el("summary", "점수 근거 보기"));
    breakdown.forEach(([k, v]) => {
      const missing = v.available === false || !finite(v.score);
      const value = `${missing ? "–" : v.score}${finite(v.max) ? `/${v.max}` : ""}`;
      const reason = missing ? "정보 없음" : typeof v.reason === "string" ? v.reason : "";
      details.append(el("p", `${SCORE_NAMES[k] || k} ${value}${reason ? ` · ${reason}` : ""}`));
    });
    // 확인 못 한 항목이 있으면 그만큼 낮춘 값이 '추천 점수'이자 순위 기준이다.
    if (finite(p.base_score) && finite(p.score) && p.base_score - p.score >= 0.05)
      details.append(el("p", `정보 보정 −${(p.base_score - p.score).toFixed(1)} · 확인 못 한 항목이 있어 ${p.base_score.toFixed(1)}점에서 낮춤`));
    inner.append(details);
  }
  if (Array.isArray(p.sources) && p.sources.length)
    inner.append(el("p", `출처 ${p.sources.filter((v) => typeof v === "string").map((v) => sourceName(v, p)).join(", ")}`, "src"));
  card.append(inner);

  // 이 장소를 기준으로 주변 다시 찾기. 서버와 같은 번호('3번 근처 맛집')로 보낸다.
  if (validCoords(p)) {
    const near = el("div", null, "near-row");
    nearTargets(p.category).forEach((target) => {
      const b = el("button", `근처 ${target}`, "near-btn");
      b.type = "button";
      b.setAttribute("aria-label", `${p.name} 근처 ${target} 찾기`);
      b.addEventListener("click", () => {
        if (busy || b.disabled) return;
        send(`${number}번 근처 ${target}`, `${p.name} 근처 ${target}`);
      });
      near.append(b);
    });
    card.append(near);
  }

  const actions = el("div", null, "card-actions");
  if (validCoords(p)) {
    const b = el("button", "지도");
    b.type = "button";
    b.setAttribute("aria-label", `${p.name} 지도에서 보기`);
    b.addEventListener("click", () => showOnMap(getEntries(), key, mock));
    actions.append(b);
  } else actions.append(el("span", "좌표 없음", "muted"));
  const info = link("장소 정보", p.url);
  if (info) actions.append(info);
  card.append(actions);
  return card;
}

/* ---------- 지도 열고 닫기 ---------- */
// PC: 지도는 처음부터 '다 펼친 크기'로 한 번만 그려 두고, 창(패널)만 부드럽게 커지며 그 지도를 드러낸다.
// 애니메이션 도중에는 지도를 다시 그리지 않으므로 끊김 없이 움직인다.
const OPEN_MS = 600;
const CLOSE_MS = 480;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let motionTimer = 0;

// 지도 칸을 고정 크기로 붙잡아 둔다. offsetY는 보이는 영역 가운데에 지도 가운데를 맞추기 위한 위쪽 여백.
function freezeMap(w, h, offsetY) {
  Object.assign($("#map").style, {
    flex: "none", width: `${w}px`, height: `${h}px`, alignSelf: "center", marginTop: `${offsetY}px`,
  });
}
function releaseMap() {
  const style = $("#map").style;
  ["flex", "width", "height", "alignSelf", "marginTop", "transition"].forEach((k) => (style[k] = ""));
}
// 애니메이션 없이 잠깐 다른 상태로 바꿔 크기를 잰 뒤 원래대로 돌린다.
function measureAs(expanded, read) {
  const panel = $("#map-panel");
  const was = panel.classList.contains("expanded");
  panel.classList.add("measuring");
  panel.classList.toggle("expanded", expanded);
  const result = read(panel);
  panel.classList.toggle("expanded", was);
  void panel.offsetWidth; // 바로 적용
  panel.classList.remove("measuring");
  return result;
}
const visibleMapHeight = (panel) => panel.clientHeight - panel.querySelector(".map-head").offsetHeight;

let sheetOpener = null; // 아래 지도 창을 연 버튼 (닫을 때 초점을 돌려줄 곳)
function openMap(open) {
  const panel = $("#map-panel");
  if (!WIDE.matches) {
    if (panel.classList.contains("open") === open) return;
    panel.classList.toggle("open", open);
    document.body.classList.toggle("sheet-open", open);
    $("#toggle-map").setAttribute("aria-expanded", String(open));
    // 아래 지도 창이 열린 동안은 뒤 대화 화면을 누르거나 Tab으로 갈 수 없게 하고(inert),
    // 초점을 '닫기'로 옮긴다. 닫으면 지도를 열었던 버튼으로 돌려준다.
    $(".room").inert = open;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", String(open));
    if (open) {
      sheetOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      $("#close-map").focus({ preventScroll: true });
    } else if (sheetOpener && document.contains(sheetOpener)) {
      sheetOpener.focus({ preventScroll: true });
      sheetOpener = null;
    }
    setTimeout(() => chatMap.refresh(open), 320);
    return;
  }
  if (panel.classList.contains("expanded") === open) return;
  clearTimeout(motionTimer);
  const setState = () => {
    panel.classList.toggle("expanded", open);
    document.body.classList.toggle("map-open", open);
    $("#map-expand").setAttribute("aria-expanded", String(open));
  };
  if (reduceMotion.matches) {
    releaseMap();
    setState();
    chatMap.refresh(false);
    return;
  }
  const mapEl = $("#map");
  if (open) {
    // 1) 다 펼쳤을 때의 지도 크기를 미리 재서, 작은 창 안에서 그 크기로 지도를 한 번 그린다.
    const { width: w, height: h } = measureAs(true, () => mapEl.getBoundingClientRect());
    freezeMap(w, h, (visibleMapHeight(panel) - h) / 2);
    chatMap.resizeKeepCenter();
    void panel.offsetWidth;
    // 2) 창이 커지는 동안 지도는 가운데를 유지한 채 점점 넓게 드러난다.
    mapEl.style.transition = `margin-top ${OPEN_MS}ms var(--ease-open)`;
    setState();
    mapEl.style.marginTop = "0px";
  } else {
    const { width: w, height: h } = mapEl.getBoundingClientRect();
    const miniVisible = measureAs(false, visibleMapHeight);
    freezeMap(w, h, 0);
    void panel.offsetWidth;
    mapEl.style.transition = `margin-top ${CLOSE_MS}ms var(--ease-close)`;
    setState();
    mapEl.style.marginTop = `${(miniVisible - h) / 2}px`;
  }
  // 3) 다 움직인 뒤 원래 배치로 돌리고, 지도를 장소들 쪽으로 부드럽게 맞춘다.
  motionTimer = setTimeout(() => {
    releaseMap();
    chatMap.resizeKeepCenter();
    // 크게 열 때만 장소 쪽으로 미끄러지듯 이동하고, 작게 줄일 때는 바로 맞춘다.
    chatMap.refresh(open);
  }, (open ? OPEN_MS : CLOSE_MS) + 40);
}
const mapIsOpen = () => $("#map-panel").classList.contains(WIDE.matches ? "expanded" : "open");
function showOnMap(entries, key, mock) {
  if (chatMap.current() !== entries) chatMap.update(entries, mock);
  openMap(true);
  setTimeout(() => chatMap.focus(key), (WIDE.matches ? OPEN_MS : 300) + 120);
}
$("#map-backdrop").addEventListener("click", () => openMap(false));
// 작게 줄이는 애니메이션이 (느린 기기·백그라운드 탭에서) 늦게 끝나도, 끝난 순간 작은 지도를 장소에 다시 맞춘다.
$("#map-panel").addEventListener("transitionend", (e) => {
  if (e.target !== e.currentTarget || e.propertyName !== "width") return;
  if (WIDE.matches && !e.currentTarget.classList.contains("expanded") && !$("#map").style.width) chatMap.refresh(false);
});
$("#toggle-map").addEventListener("click", () => openMap(!mapIsOpen()));
$("#map-expand").addEventListener("click", () => openMap(true));
$("#close-map").addEventListener("click", () => openMap(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && mapIsOpen()) openMap(false);
});
// 닫기 버튼 이름: PC는 지도를 다시 작게, 좁은 화면은 아래 창을 닫는다.
const setCloseLabel = () => ($("#close-map").textContent = WIDE.matches ? "작게 보기" : "닫기");
setCloseLabel();
WIDE.addEventListener("change", () => {
  setCloseLabel();
  clearTimeout(motionTimer);
  releaseMap();
  $("#map-panel").classList.remove("open", "expanded");
  document.body.classList.remove("map-open", "sheet-open");
  $("#toggle-map").setAttribute("aria-expanded", "false");
  $(".room").inert = false;
  $("#map-panel").removeAttribute("role");
  $("#map-panel").removeAttribute("aria-modal");
  sheetOpener = null;
  setTimeout(() => chatMap.refresh(), 50);
});

/* ---------- 새 대화 ---------- */
function welcome() {
  chatLog.replaceChildren();
  addCenter(dayFormat.format(new Date()), "day-divider");
  const { body } = botMessage();
  addBubble(body, `안녕하세요, ${BOT_NAME}예요! 부산에서 가고 싶은 곳을 편하게 말해 주세요.`);
  addBubble(body, "예를 들어 “서면에서 친구랑 갈 조용한 카페”처럼 동네와 조건을 함께 말해 주시면 더 잘 찾아요.");
  stamp(body);
  chatLog.append(startCredit());
  setQuickReplies(STARTERS);
}
// 첫 화면 맨 아래 작은 안내. 첫 메시지를 보내면 지웁니다 (지도 영역의 출처 표시는 계속 남습니다).
function startCredit() {
  const box = el("div", null, "start-credit");
  box.id = "start-credit";
  box.append(
    el("p", `${BOT_NAME}의 답은 틀릴 수 있어요. 방문 전에 장소 정보를 한 번 더 확인해 주세요.`),
    el("p", DATA_CREDIT),
  );
  return box;
}
function retireNearButtons(reason) {
  document.querySelectorAll(".near-btn").forEach((b) => {
    b.disabled = true;
    b.title = reason;
  });
}
function resetSession() {
  sessionId = newId();
  lastRequest = null;
}
$("#new-chat").addEventListener("click", () => {
  if (busy) return;
  resetSession();
  if (mapIsOpen()) openMap(false); // 새 대화에는 표시할 장소가 없으니 펼친 지도도 접는다
  chatMap.update([]);
  welcome();
  input.focus();
});

/* ---------- 현재 위치 ---------- */
function setLocationUI(text, active) {
  $("#location-bar").hidden = !text;
  $("#location-status").textContent = text || "";
  $("#clear-location").hidden = !active;
  $("#locate").setAttribute("aria-pressed", String(active));
  $("#locate").setAttribute("aria-label", active ? "현재 위치 다시 확인" : "현재 위치 사용");
}
function clearLocation() {
  gpsVersion++;
  coords = null;
  resetSession();
  setLocationUI("", false);
  // 위치가 남지 않게 서버 대화를 새로 시작하므로, 이전 결과에 이어지는 버튼은 더 쓸 수 없다.
  retireNearButtons("위치 해제 전 결과라 쓸 수 없어요");
  setQuickReplies([]);
  addCenter("위치 사용을 껐어요. 이전 결과와는 이어지지 않는 새 대화로 시작해요.", "system-msg");
}
$("#clear-location").addEventListener("click", clearLocation);
$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation || !window.isSecureContext) {
    setLocationUI("현재 위치는 HTTPS 주소에서만 쓸 수 있어요. 질문에 지역을 적어 주세요.", false);
    return;
  }
  const version = ++gpsVersion;
  setLocationUI("위치 확인 중… 브라우저의 위치 권한을 허용해 주세요.", Boolean(coords));
  navigator.geolocation.getCurrentPosition(
    (position) => {
      if (version !== gpsVersion) return;
      const point = { latitude: position.coords.latitude, longitude: position.coords.longitude };
      if (!validCoords(point)) {
        setLocationUI("위치를 얻지 못했어요. 다시 누르거나 지역을 입력해 주세요.", Boolean(coords));
        return;
      }
      coords = point;
      setLocationUI("현재 위치를 함께 보내고 있어요.", true);
    },
    (e) => {
      if (version !== gpsVersion) return;
      const reason =
        {
          1: "위치 권한이 거부됐어요. 질문에 지역을 적어 주세요.",
          2: "위치를 확인할 수 없어요. 다시 누르거나 지역을 입력해 주세요.",
          3: "위치 확인 시간이 지났어요. 다시 눌러 주세요.",
        }[e.code] || "위치 확인에 실패했어요.";
      setLocationUI(coords ? `${reason} 이전 위치를 계속 써요.` : reason, Boolean(coords));
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 0 },
  );
});

welcome();
