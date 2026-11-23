import { el, validCoords } from "./ui-utils.js";
// 메신저 화면용 지도. 카드 번호와 같은 번호의 핀을 표시합니다.
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const BUSAN_CENTER = [35.1796, 129.0756];
export function createChatMap() {
  let map,
    layer,
    tiles,
    entries = [],
    markers = new Map(),
    points = [],
    tileTimer,
    isMock = false;
  const status = document.querySelector("#map-status");
  const resultStatus = document.querySelector("#map-result-status");
  function init() {
    if (!window.L) {
      status.textContent =
        "지도 프로그램을 불러오지 못했어요. 새로고침해 주세요. 대화는 계속할 수 있어요.";
      return;
    }
    try {
      if (!map) {
        // 부산 기본 화면일 뿐 사용자 위치를 서버로 보내지 않습니다.
        map = L.map("map", { zoomSnap: 0.25 }).setView(BUSAN_CENTER, 11);
        layer = L.layerGroup().addTo(map);
        tiles = L.tileLayer(TILE_URL, {
          maxZoom: 19,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);
        tiles.on("tileerror", () => {
          clearTimeout(tileTimer);
          status.textContent = "지도 배경을 불러오지 못했어요. 인터넷 연결을 확인하고 다시 표시해 주세요.";
        });
        tiles.on("tileload", () => {
          clearTimeout(tileTimer);
          status.textContent = "핀을 누르면 장소 이름을 볼 수 있어요.";
        });
      } else tiles.redraw();
      status.textContent = "지도 배경을 불러오는 중이에요…";
      clearTimeout(tileTimer);
      tileTimer = setTimeout(() => {
        status.textContent = "지도 로딩이 늦어지고 있어요. 다시 표시를 눌러 주세요.";
      }, 15000);
      update(entries, isMock);
    } catch {
      status.textContent = "지도를 표시하지 못했어요. 장소 카드는 계속 볼 수 있어요.";
    }
  }
  // smooth=true면 줌·이동을 애니메이션으로 (지도를 크게 열었을 때 장소 쪽으로 미끄러지듯 이동)
  function fit(smooth = false) {
    if (!map) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (points.length) {
      if (smooth && !reduce) map.flyToBounds(points, { padding: [40, 40], maxZoom: 15, duration: 0.6 });
      else map.fitBounds(points, { padding: [40, 40], maxZoom: 15, animate: false });
    } else map.setView(BUSAN_CENTER, 11, { animate: false });
  }
  function update(nextEntries, mock = false) {
    entries = nextEntries;
    isMock = mock;
    if (!map || !layer) return;
    layer.clearLayers();
    markers.clear();
    points = [];
    entries.forEach(({ place, key, card, number }) => {
      if (!validCoords(place)) return;
      const pin = el("span", null, "pin");
      pin.append(el("b", String(number)));
      const icon = L.divIcon({
        className: "map-pin",
        html: pin,
        iconSize: [28, 28],
        iconAnchor: [14, 32],
        popupAnchor: [0, -30],
      });
      const popup = el("div");
      popup.append(el("strong", `${number}. ${place.name}`));
      if (place.address) popup.append(el("p", place.address));
      const label = `${number}. ${place.name}`;
      const marker = L.marker([place.latitude, place.longitude], { icon, title: label, alt: label, keyboard: true })
        .addTo(layer)
        .bindPopup(popup);
      marker.on("popupopen", () => {
        document.querySelectorAll(".place-card.selected").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
      });
      markers.set(key, marker);
      points.push([place.latitude, place.longitude]);
    });
    fit();
    resultStatus.textContent = entries.length
      ? `${entries.length}곳 중 ${points.length}곳을 지도에 표시했어요.${
          entries.length > points.length ? " 좌표가 없는 곳은 카드에서 확인하세요." : ""
        }${mock ? " 샘플 위치라 실제 장소가 아니에요." : ""}`
      : "부기가 찾은 장소가 번호와 함께 여기에 표시돼요.";
  }
  // 숨겨져 있던 지도를 다시 보여줄 때 크기를 다시 계산합니다.
  function refresh(smooth = false) {
    if (!map) return;
    map.invalidateSize({ pan: false });
    fit(smooth);
  }
  // 지도 칸 크기만 바뀌었을 때: 보던 위치는 그대로 두고 크기만 다시 계산한다.
  // 지도 칸 크기가 바뀌어도 보이는 가운데 지점은 그대로 두고 다시 그린다.
  function resizeKeepCenter() {
    if (map) map.invalidateSize({ pan: true, animate: false });
  }

  function resize() {
    if (map) map.invalidateSize({ pan: false, animate: false });
  }
  function focus(key) {
    const marker = markers.get(key);
    if (!marker) return;
    map.invalidateSize({ pan: false });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      map.setView(marker.getLatLng(), 16, { animate: false });
      marker.openPopup();
      return;
    }
    map.once("moveend", () => marker.openPopup());
    map.flyTo(marker.getLatLng(), 16, { duration: 0.7 });
  }
  document.querySelector("#map-retry").addEventListener("click", init);
  init();
  return { update, refresh, resize, resizeKeepCenter, focus, current: () => entries, available: () => Boolean(map) };
}
