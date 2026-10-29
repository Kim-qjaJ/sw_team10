import { el, validCoords } from "./ui-utils.js";
// 서비스 교체 시 이 설정과 저작자 표시를 함께 변경합니다.
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export function createPlaceMap() {
  let map,
    layer,
    tiles,
    entries = [],
    markers = new Map(),
    tileTimer,
    isMock = false;
  const status = document.querySelector("#map-status");
  const resultStatus = document.querySelector("#map-result-status");
  function init() {
    if (!window.L) {
      status.textContent =
        "지도 프로그램을 불러오지 못했어요. 페이지를 새로고침해 주세요. 장소 검색은 계속 사용할 수 있어요.";
      return;
    }
    try {
      if (!map) {
        // 부산 기본 뷰일 뿐 사용자 위치나 장소 좌표로 전송하지 않습니다.
        map = L.map("map", {
          scrollWheelZoom: false,
          zoomAnimation: false,
          fadeAnimation: false,
          markerZoomAnimation: false,
        }).setView([35.1796, 129.0756], 11);
        layer = L.layerGroup().addTo(map);
        tiles = L.tileLayer(TILE_URL, {
          maxZoom: 19,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);
        tiles.on("tileerror", () => {
          clearTimeout(tileTimer);
          status.textContent =
            "지도 타일을 불러오지 못했어요. 인터넷 연결을 확인하고 지도를 다시 표시해 주세요.";
        });
        tiles.on("tileload", () => {
          clearTimeout(tileTimer);
          status.textContent =
            "장소 표시를 누르면 정보를 볼 수 있어요. + / − 버튼으로 확대·축소하세요.";
        });
      } else tiles.redraw();
      status.textContent = "지도 배경을 불러오는 중이에요…";
      clearTimeout(tileTimer);
      tileTimer = setTimeout(() => {
        status.textContent =
          "지도 로딩이 지연되고 있어요. 인터넷 연결을 확인한 뒤 다시 표시해 주세요.";
      }, 15000);
      update(entries, isMock);
    } catch {
      status.textContent =
        "지도를 표시하지 못했어요. 장소 목록은 계속 이용할 수 있어요.";
    }
  }
  function update(nextEntries, mock = false) {
    entries = nextEntries;
    isMock = mock;
    if (!map || !layer) return;
    layer.clearLayers();
    markers.clear();
    const points = [];
    entries.forEach(({ place, key, card, number }) => {
      if (!validCoords(place)) return;
      const popup = el("div");
      popup.append(el("strong", `${number}. ${place.name}`));
      if (place.address) popup.append(el("p", place.address));
      const marker = L.circleMarker([place.latitude, place.longitude], {
        radius: 9,
        color: "#fff",
        weight: 2,
        fillColor: "#B8860B",
        fillOpacity: 1,
      })
        .addTo(layer)
        .bindPopup(popup);
      // CircleMarker SVG에 키보드 접근과 읽을 수 있는 이름을 제공합니다.
      const node = marker.getElement();
      if (node) {
        node.setAttribute("tabindex", "0");
        node.setAttribute("role", "button");
        node.setAttribute("aria-label", `${number}. ${place.name}`);
        node.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            marker.openPopup();
          }
        });
      }
      marker.on("popupopen", () => {
        document
          .querySelectorAll(".place-card.selected")
          .forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
      });
      markers.set(key, marker);
      points.push([place.latitude, place.longitude]);
    });
    if (points.length)
      map.fitBounds(points, { padding: [35, 35], maxZoom: 15 });
    else map.setView([35.1796, 129.0756], 11);
    resultStatus.textContent = entries.length
      ? `전체 ${entries.length}곳 중 ${points.length}곳 표시 · 좌표 없는 ${entries.length - points.length}곳은 목록에서 확인하세요.${mock ? " 샘플 위치이며 실제 장소가 아닙니다." : ""}`
      : "검색하면 좌표가 있는 장소가 여기에 표시돼요.";
  }
  function focus(key) {
    const marker = markers.get(key);
    if (!marker) return;
    document
      .querySelector("#map-title")
      .scrollIntoView({ block: "center", behavior: "instant" });
    map.invalidateSize();
    map.setView(marker.getLatLng(), 16);
    marker.openPopup();
  }
  document.querySelector("#map-retry").addEventListener("click", init);
  init();
  return { update, focus, available: () => Boolean(map) };
}
