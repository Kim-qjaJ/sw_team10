// API 데이터는 HTML 문자열 대신 텍스트 노드로만 출력합니다.
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export const finite = (value) =>
  typeof value === "number" && Number.isFinite(value);
export const validCoords = (p) =>
  finite(p.latitude) &&
  finite(p.longitude) &&
  Math.abs(p.latitude) <= 90 &&
  Math.abs(p.longitude) <= 180;
export function safeURL(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
export function link(label, value) {
  const url = safeURL(value);
  if (!url) return null;
  const a = el("a", label);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}
export function validateResponse(data) {
  if (
    !data ||
    !data.intent ||
    typeof data.intent.intent !== "string" ||
    !Array.isArray(data.results) ||
    typeof data.mock_data !== "boolean"
  )
    throw new Error(
      "서버 응답 형식이 올바르지 않아요. 잠시 후 다시 시도해 주세요.",
    );
  for (const result of data.results) {
    if (
      !result ||
      !result.request ||
      typeof result.request.query !== "string" ||
      !Array.isArray(result.places) ||
      result.places.some((p) => !p || typeof p.name !== "string")
    )
      throw new Error("장소 정보를 읽을 수 없어요. 다시 시도해 주세요.");
  }
  for (const field of ["message", "notice"])
    if (data[field] != null && typeof data[field] !== "string")
      throw new Error("안내 메시지 형식이 올바르지 않아요.");
  if (
    data.suggestions != null &&
    (!Array.isArray(data.suggestions) ||
      data.suggestions.some(
        (s) =>
          !s || typeof s.label !== "string" || typeof s.message !== "string",
      ))
  )
    throw new Error("후속 질문 형식이 올바르지 않아요.");
  if (
    data.links != null &&
    (!Array.isArray(data.links) ||
      data.links.some(
        (s) => !s || typeof s.label !== "string" || typeof s.url !== "string",
      ))
  )
    throw new Error("링크 형식이 올바르지 않아요.");
  return data;
}
export const categoryNames = {
  restaurant: "음식점",
  cafe: "카페",
  culture: "문화시설",
  tourism: "관광",
  activity: "체험",
  public_facility: "공공시설",
  education: "교육",
  other: "기타",
};
