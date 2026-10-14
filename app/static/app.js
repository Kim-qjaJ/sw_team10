import {
  el,
  finite,
  validCoords,
  link,
  validateResponse,
  categoryNames,
} from "./ui-utils.js";
import { createPlaceMap } from "./map.js";
import { initImagePreview } from "./image-upload.js";
const SERVICE_NAME = "부산 한걸음"; // 임시 서비스명: 확정되면 이 값과 HTML 기본 제목을 변경합니다.
const REQUEST_TIMEOUT_MS = 45000;
const $ = (selector) => document.querySelector(selector);
document
  .querySelectorAll("[data-service-name]")
  .forEach((n) => (n.textContent = SERVICE_NAME));
document.title = `${SERVICE_NAME} · 부산 장소 안내`;
let busy = false,
  coords = null,
  gpsVersion = 0,
  sessionId,
  lastRequest;
function newId() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
function saveSession() {
  try {
    sessionStorage.setItem("busan-session", sessionId);
  } catch {
    /* 저장 불가 환경에서는 메모리만 사용 */
  }
}
try {
  sessionId = sessionStorage.getItem("busan-session");
} catch {
  /* private mode */
}
if (!sessionId || sessionId.length > 100) sessionId = newId();
saveSession();
const placeMap = createPlaceMap();
initImagePreview();
const mode = () => $("input[name=mode]:checked").value;
function updateMode() {
  $("#category-wrap").hidden = mode() !== "recommend";
  $("#mode-help").textContent = {
    search: "이름을 아는 장소를 빠르게 찾아보세요.",
    recommend: "동네와 취향을 알려주시면 장소를 추천해 드려요.",
    nearby: "현재 위치를 사용하거나 “서면역 약국”처럼 기준 지역을 적어 주세요.",
  }[mode()];
}
document
  .querySelectorAll("[name=mode]")
  .forEach((n) => n.addEventListener("change", updateMode));
function setMode(value) {
  const radio = [...document.querySelectorAll("[name=mode]")].find(
    (n) => n.value === value,
  );
  if (radio) {
    radio.checked = true;
    updateMode();
  }
}
function count() {
  $("#char-count").textContent = `${[...$("#message").value].length} / 500`;
}
$("#message").addEventListener("input", count);
$("#message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#search-form").requestSubmit();
  }
});
document.querySelectorAll("[data-query]").forEach((b) =>
  b.addEventListener("click", () => {
    if (busy) return;
    $("#message").value = b.dataset.query;
    $("#category").value = "";
    setMode("recommend");
    count();
    $("#message").focus();
  }),
);
function setBusy(value) {
  busy = value;
  $("#submit").disabled = value;
  $("#submit").textContent = value ? "검색하고 있어요…" : "장소 찾아보기 →";
  $(".results-section").setAttribute("aria-busy", String(value));
  document
    .querySelectorAll("#suggestions button,#retry,#new-session,#clear-location")
    .forEach((n) => (n.disabled = value));
  $("#feedback").hidden = !value;
  $("#feedback").textContent = value
    ? "조건을 확인하고 장소를 찾고 있어요. 잠시만 기다려 주세요."
    : "";
}
function error(message) {
  $("#error").hidden = false;
  $("#error-text").textContent = message;
}
function requestPayload() {
  const payload = {
    message: $("#message").value.trim(),
    limit: Number($("#limit").value),
    session_id: sessionId,
    mode: mode(),
  };
  if (mode() === "recommend" && $("#category").value)
    payload.category = $("#category").value;
  if (coords) Object.assign(payload, coords);
  return payload;
}
async function search(payload) {
  if (busy) return;
  $("#error").hidden = true;
  if (!payload.message || [...payload.message].length > 500) {
    lastRequest = null;
    $("#retry").hidden = true;
    error("질문을 1~500자로 입력해 주세요. 예: 서면 카페 추천해줘");
    $("#message").focus();
    return;
  }
  lastRequest = payload;
  $("#retry").hidden = false;
  setBusy(true);
  // 새 요청 중에는 이전 결과가 현재 검색 결과로 오해되지 않게 비웁니다.
  $("#answer").hidden = true;
  $("#results").replaceChildren();
  placeMap.update([]);
  $("#result-count").textContent = "검색 중";
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/api/v1/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok)
      throw new Error(
        response.status === 429
          ? "요청이 많아 잠시 쉬고 있어요. 잠시 후 다시 시도해 주세요."
          : response.status === 422
            ? "입력 조건을 확인해 주세요. 질문은 500자 이하로 입력해야 해요."
            : `서버가 요청을 처리하지 못했어요 (${response.status}). 잠시 후 다시 시도해 주세요.`,
      );
    let data;
    try {
      data = await response.json();
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error(
        "서버에서 올바른 JSON 응답을 받지 못했어요. 다시 시도해 주세요.",
      );
    }
    render(validateResponse(data));
  } catch (e) {
    $("#result-count").textContent = "검색 실패";
    error(
      e.name === "AbortError"
        ? "응답을 45초 동안 받지 못했어요. 잠시 후 다시 시도해 주세요."
        : e instanceof TypeError
          ? "서버에 연결할 수 없어요. 인터넷 연결과 서버 실행 상태를 확인해 주세요."
          : e.message,
    );
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
}
$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  search(requestPayload());
});
$("#retry").addEventListener("click", () => {
  if (lastRequest) search(lastRequest);
});
const resultNames = {
  exact: "특정 장소",
  brand: "브랜드 검색",
  search: "검색 결과",
  recommend: "추천 후보",
};
function render(data) {
  $("#answer").hidden = false;
  $("#mock-label").hidden = !data.mock_data;
  $("#answer-message").textContent =
    data.message || data.intent.question || "검색 결과를 확인해 주세요.";
  const notices = [data.notice];
  const recommended = data.results
    .filter((r) => r.result_type === "recommend")
    .flatMap((r) => r.places);
  if (
    recommended.length &&
    recommended.every(
      (p) =>
        !p.score_breakdown ||
        !Object.values(p.score_breakdown).some(
          (v) => v && v.available === true && finite(v.score),
        ),
    )
  ) {
    notices.push(
      "이 결과에는 계산된 점수 근거가 제공되지 않았어요. 서버 안내의 순서 표현을 실제 평가 순위로 보지 마세요.",
    );
  }
  $("#notice").textContent = notices.filter(Boolean).join("\n");
  $("#notice").hidden = !$("#notice").textContent;
  $("#response-meta").textContent = [
    data.intent.location,
    finite(data.radius_m)
      ? `검색 반경 ${data.radius_m.toLocaleString()}m`
      : null,
    finite(data.timing_ms?.total_ms)
      ? `서버 처리 ${(data.timing_ms.total_ms / 1000).toFixed(2)}초`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  $("#response-links").replaceChildren();
  (data.links || []).forEach((item) => {
    const a = link(item.label, item.url);
    if (a) $("#response-links").append(a);
  });
  $("#suggestions").replaceChildren();
  (data.suggestions || []).forEach((s) => {
    const b = el("button", s.label);
    b.type = "button";
    b.addEventListener("click", () => {
      if (busy) return;
      if (s.mode) setMode(s.mode);
      if (s.message.trim()) {
        $("#message").value = s.message;
        count();
        search(requestPayload());
      } else $("#message").focus();
    });
    $("#suggestions").append(b);
  });
  const entries = [];
  let number = 0;
  $("#results").replaceChildren();
  data.results.forEach((result, groupIndex) => {
    const group = el("section", null, "group"),
      heading = el("h3", result.request.query, "group-title");
    heading.append(
      el(
        "span",
        `${resultNames[result.result_type] || "장소"} · ${result.places.length}곳`,
        "tag",
      ),
    );
    group.append(heading);
    const cards = el("div", null, "cards");
    result.places.forEach((place, index) => {
      number++;
      const key = `${groupIndex}-${index}`,
        card = makeCard(place, key, number, result.result_type);
      cards.append(card);
      entries.push({ place, key, card, number });
    });
    if (!result.places.length)
      group.append(
        el(
          "p",
          "조건에 맞는 장소가 없어요. 지역이나 장소 종류를 바꿔 보세요.",
          "hint",
        ),
      );
    group.append(cards);
    $("#results").append(group);
  });
  if (!number) {
    const empty = el("div", null, "empty panel");
    empty.append(
      el(
        "h3",
        data.intent.intent === "clarify"
          ? "조금 더 알려주세요"
          : "표시할 장소가 없어요",
      ),
      el("p", "위 안내를 확인하고 지역이나 장소 종류를 입력해 주세요."),
    );
    $("#results").append(empty);
  }
  $("#result-count").textContent =
    `${data.results.length}개 검색 묶음 · ${number}곳${data.mock_data ? " · 샘플 데이터" : ""}`;
  placeMap.update(entries, data.mock_data);
}
function makeCard(p, key, number, resultType) {
  const card = el("article", null, "place-card panel"),
    top = el("div", null, "card-top");
  top.append(
    el("span", number, "place-number"),
    el(
      "span",
      categoryNames[p.category] || p.category || "분류 정보 없음",
      "tag",
    ),
  );
  card.append(top, el("h3", p.name));
  card.append(el("p", p.address || "주소 정보 없음"));
  if (p.phone) card.append(el("p", `전화 ${p.phone}`));
  const metrics = [];
  if (finite(p.distance_m) && p.distance_m >= 0)
    metrics.push(
      p.distance_m >= 1000
        ? `${(p.distance_m / 1000).toFixed(1)}km`
        : `${Math.round(p.distance_m)}m`,
    );
  [
    ["walking_minutes", "도보"],
    ["transit_minutes", "대중교통"],
    ["driving_minutes", "자동차"],
  ].forEach(([k, label]) => {
    if (finite(p[k]) && p[k] >= 0)
      metrics.push(`${label} ${Math.round(p[k])}분`);
  });
  if (metrics.length) card.append(el("p", metrics.join(" · "), "metrics"));
  const breakdown =
    p.score_breakdown && typeof p.score_breakdown === "object"
      ? Object.entries(p.score_breakdown).filter(
          ([, v]) => v && typeof v === "object",
        )
      : [];
  // 기본값 0점은 숨기고 계산 근거가 있는 추천 점수만 표시합니다. 만점/평점 척도는 추정하지 않습니다.
  if (
    resultType === "recommend" &&
    finite(p.score) &&
    breakdown.some(([, v]) => v.available === true && finite(v.score))
  ) {
    card.append(el("p", `추천 점수 ${p.score.toFixed(1)}`));
  }
  if (breakdown.length) {
    const details = el("details");
    details.append(el("summary", "점수 세부 정보"));
    breakdown.forEach(([k, v]) =>
      details.append(
        el(
          "p",
          `${k}: ${v.available === false ? "정보 없음" : finite(v.score) ? v.score : "—"}${finite(v.max) ? ` / ${v.max}` : ""}${typeof v.reason === "string" ? ` · ${v.reason}` : ""}`,
        ),
      ),
    );
    card.append(details);
  }
  if (Array.isArray(p.sources) && p.sources.length)
    card.append(
      el(
        "p",
        `출처: ${p.sources.filter((v) => typeof v === "string").join(", ")}`,
        "hint",
      ),
    );
  const actions = el("div", null, "button-row"),
    a = link("장소 정보 ↗", p.url);
  if (a) actions.append(a);
  if (validCoords(p) && placeMap.available()) {
    const b = el("button", "지도에서 보기");
    b.type = "button";
    b.setAttribute("aria-label", `${p.name} 지도에서 보기`);
    b.addEventListener("click", () => placeMap.focus(key));
    actions.append(b);
    card.addEventListener("click", (e) => {
      if (!e.target.closest("button,a,summary,details")) placeMap.focus(key);
    });
  } else
    actions.append(
      el(
        "span",
        validCoords(p) ? "지도 연결을 확인해 주세요" : "좌표 정보 없음",
        "hint",
      ),
    );
  card.append(actions);
  return card;
}
function resetSession() {
  sessionId = newId();
  saveSession();
  lastRequest = null;
  $("#retry").hidden = true;
}
$("#new-session").addEventListener("click", () => {
  resetSession();
  $("#answer").hidden = true;
  $("#results").replaceChildren();
  placeMap.update([]);
  $("#error").hidden = true;
  $("#result-count").textContent = "새 대화를 시작했어요";
  $("#message").value = "";
  count();
  $("#message").focus();
});
$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation || !window.isSecureContext) {
    $("#location-status").textContent =
      "현재 위치는 HTTPS 또는 localhost에서 사용할 수 있어요. 질문에 지역을 직접 적어 주세요.";
    return;
  }
  const version = ++gpsVersion;
  $("#locate").disabled = true;
  $("#clear-location").hidden = false;
  $("#location-status").textContent =
    "위치 확인 중… 브라우저의 위치 권한을 허용해 주세요.";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      if (version !== gpsVersion) return;
      const point = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      $("#locate").disabled = false;
      if (!validCoords(point)) {
        $("#location-status").textContent =
          "유효한 위치를 얻지 못했어요. 다시 요청하거나 지역을 입력해 주세요.";
        return;
      }
      coords = point;
      $("#locate").textContent = "◎ 위치 다시 확인";
      $("#location-status").textContent =
        "현재 위치를 확인했어요. 다음 검색부터 함께 전송합니다. 부산 밖에서는 부산의 기준 장소를 입력해 주세요.";
    },
    (e) => {
      if (version !== gpsVersion) return;
      $("#locate").disabled = false;
      $("#location-status").textContent =
        ({
          1: "위치 권한이 거부되었어요. 브라우저 설정에서 허용하거나 질문에 지역을 입력해 주세요.",
          2: "위치를 확인할 수 없어요. 다시 요청하거나 지역을 입력해 주세요.",
          3: "위치 확인 시간이 초과됐어요. 다시 요청하거나 지역을 입력해 주세요.",
        }[e.code] || "위치 확인에 실패했어요.") +
        (coords
          ? " 이전에 확인한 위치를 유지합니다. 원치 않으면 위치를 해제하세요."
          : "");
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 0 },
  );
});
$("#clear-location").addEventListener("click", () => {
  gpsVersion++;
  coords = null;
  resetSession();
  $("#locate").disabled = false;
  $("#locate").textContent = "◎ 현재 위치 사용";
  $("#clear-location").hidden = true;
  $("#location-status").textContent =
    "위치를 해제하고 새 대화를 시작했어요. 질문에 지역을 입력해 주세요.";
});
