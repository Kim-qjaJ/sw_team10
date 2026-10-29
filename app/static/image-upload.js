// 후속 이미지 API 연결은 이 모듈에서 구현합니다. 현재 네트워크 전송은 없습니다.
export function initImagePreview() {
  const input = document.querySelector("#image-input"),
    preview = document.querySelector("#image-preview"),
    wrap = document.querySelector("#image-preview-wrap"),
    status = document.querySelector("#image-status");
  let objectURL;
  function clear() {
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    preview.removeAttribute("src");
    input.value = "";
    wrap.hidden = true;
  }
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    clear();
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      status.textContent = "JPG, PNG, WebP 이미지 파일만 선택할 수 있어요.";
      return;
    }
    if (file.size > 5 * 1024 * 1024 || file.size === 0) {
      status.textContent = "0바이트보다 크고 5MB 이하인 사진을 선택해 주세요.";
      return;
    }
    status.textContent = "미리보기를 불러오는 중이에요…";
    objectURL = URL.createObjectURL(file);
    preview.onload = () => {
      wrap.hidden = false;
      status.textContent =
        "미리보기 완료 · 사진은 전송되지 않았어요. AI 분석은 준비 중입니다.";
    };
    preview.onerror = () => {
      clear();
      status.textContent =
        "이미지를 읽을 수 없어요. 다른 사진을 선택해 주세요.";
    };
    document.querySelector("#image-name").textContent = file.name;
    preview.src = objectURL;
  });
  document.querySelector("#remove-image").addEventListener("click", () => {
    clear();
    status.textContent = "사진을 제거했어요. 다른 사진을 선택할 수 있어요.";
    input.focus();
  });
  window.addEventListener("pagehide", clear);
}
