const BUCHAREST_CENTER = [44.4268, 26.1025];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

document.addEventListener("DOMContentLoaded", () => {
  const homeMap = document.getElementById("map");
  if (homeMap) initHomeMap(homeMap);

  const matchMap = document.getElementById("match-map");
  if (matchMap) initMatchMap(matchMap);

  const picker = document.getElementById("pitch-picker");
  if (picker) initPitchPicker(picker);
});

/* ---------- Helpers ---------- */

// Builds DOM elements with textContent (never innerHTML),
// so names typed by users can't inject HTML or scripts.
function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function createBaseMap(element, center, zoom, scrollWheelZoom = true) {
  const map = L.map(element, { scrollWheelZoom }).setView(center, zoom);
  L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map);
  return map;
}

function availability(spotsLeft) {
  if (spotsLeft === 0) return "full";
  if (spotsLeft <= 2) return "few";
  return "open";
}

function spotsText(spotsLeft) {
  if (spotsLeft === 0) return "Full";
  return spotsLeft === 1 ? "1 spot left" : `${spotsLeft} spots left`;
}

function priceText(price) {
  const value = Number(price);
  if (value === 0) return "Free";
  return `${value.toLocaleString("en", { maximumFractionDigits: 2 })} RON`;
}

function spotDots(taken, total) {
  const wrapper = el("span", "spot-dots");
  wrapper.setAttribute("aria-label", `${taken} of ${total} spots taken`);
  for (let i = 0; i < total; i++) {
    wrapper.appendChild(el("i", i < taken ? "dot dot--taken" : "dot"));
  }
  return wrapper;
}

function pitchIcon(openSpots) {
  return L.divIcon({
    className: "",
    html: `<span class="pitch-marker pitch-marker--${availability(openSpots)}">${openSpots}</span>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -20],
  });
}

/* ---------- Home page: map + list ---------- */

async function initHomeMap(element) {
  const map = createBaseMap(element, BUCHAREST_CENTER, 12);
  const list = document.getElementById("match-list");
  const summary = document.getElementById("match-summary");

  let data;
  try {
    const response = await fetch(element.dataset.apiUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    console.error("Could not load pitches:", error);
    summary.textContent = "Matches could not be loaded. Refresh the page to try again.";
    list.replaceChildren();
    return;
  }

  if (data.pitches.length === 0) {
    summary.textContent = "No upcoming matches yet.";
    list.replaceChildren(el("p", "list-message", "When an organizer schedules a match, it will show up here."));
    return;
  }

  // Markers: one per pitch, showing the total number of open spots
  const markers = new Map();
  data.pitches.forEach((pitch) => {
    const openSpots = pitch.matches.reduce((sum, match) => sum + match.spots_left, 0);
    const marker = L.marker([pitch.lat, pitch.lng], { icon: pitchIcon(openSpots), title: pitch.name })
      .bindPopup(buildPopup(pitch), { maxWidth: 300 })
      .addTo(map);
    markers.set(pitch.id, marker);
  });

  const group = L.featureGroup([...markers.values()]);
  map.fitBounds(group.getBounds().pad(0.2), { maxZoom: 14 });

  // List: every match, sorted by start time and grouped by day
  const matches = data.pitches
    .flatMap((pitch) => pitch.matches.map((match) => ({ ...match, pitch })))
    .sort((a, b) => a.start_iso.localeCompare(b.start_iso));

  const totalOpen = matches.reduce((sum, match) => sum + match.spots_left, 0);
  summary.textContent =
    `${matches.length} upcoming ${matches.length === 1 ? "match" : "matches"}, ` +
    `${totalOpen} open ${totalOpen === 1 ? "spot" : "spots"}`;

  list.replaceChildren();
  let currentDay = null;

  matches.forEach((match) => {
    if (match.day !== currentDay) {
      currentDay = match.day;
      list.appendChild(el("h2", "list-day", match.day));
    }
    list.appendChild(buildListItem(match, () => focusPitch(map, element, markers.get(match.pitch.id))));
  });
}

function buildListItem(match, onSelect) {
  const item = el("button", `match-item match-item--${availability(match.spots_left)}`);
  item.type = "button";

  const body = el("span", "match-item__body");
  body.appendChild(el("span", "match-item__pitch", match.pitch.name));
  body.appendChild(el("span", "match-item__meta", `${match.pitch.sector}, ${priceText(match.price)}`));

  const spots = el("span", "match-item__spots");
  spots.append(spotDots(match.spots_taken, match.spots_needed), el("span", "", spotsText(match.spots_left)));
  body.appendChild(spots);

  item.append(el("span", "match-item__time", match.time), body);
  item.addEventListener("click", onSelect);
  return item;
}

function focusPitch(map, mapElement, marker) {
  if (REDUCE_MOTION) {
    map.setView(marker.getLatLng(), 15);
  } else {
    map.flyTo(marker.getLatLng(), 15, { duration: 0.6 });
  }
  marker.openPopup();

  // On phones the list sits under the map, so scroll back up to it
  if (window.matchMedia("(max-width: 991.98px)").matches) {
    mapElement.scrollIntoView({ behavior: REDUCE_MOTION ? "auto" : "smooth", block: "center" });
  }
}

function buildPopup(pitch) {
  const box = el("div");
  box.appendChild(el("h3", "popup__title", pitch.name));
  box.appendChild(el("p", "popup__address", `${pitch.address}, ${pitch.sector}`));

  pitch.matches.forEach((match) => {
    const row = el("div", "popup__match");
    const info = el("div");
    info.appendChild(el("div", "popup__when", `${match.day}, ${match.time}`));

    const spots = el("div", "popup__spots");
    spots.append(spotDots(match.spots_taken, match.spots_needed), el("span", "", spotsText(match.spots_left)));
    info.appendChild(spots);

    const link = el("a", "btn btn-success btn-sm", "View");
    link.href = match.url;

    row.append(info, link);
    box.appendChild(row);
  });

  return box;
}

/* ---------- Match page: small location map ---------- */

function initMatchMap(element) {
  const position = [Number(element.dataset.lat), Number(element.dataset.lng)];
  const map = createBaseMap(element, position, 15, false);
  L.marker(position, { icon: pitchIcon(Number(element.dataset.spotsLeft)) }).addTo(map);
}

/* ---------- Add pitch: click the map to set the location ---------- */

function initPitchPicker(element) {
  const latInput = document.getElementById(element.dataset.latInput);
  const lngInput = document.getElementById(element.dataset.lngInput);
  const status = document.getElementById("picker-status");

  // If the form was re-shown after an error, keep the point that was already chosen
  const hasValue = latInput.value !== "" && lngInput.value !== "";
  const start = hasValue ? [Number(latInput.value), Number(lngInput.value)] : BUCHAREST_CENTER;
  const map = createBaseMap(element, start, hasValue ? 16 : 12);
  let marker = null;

  function place(latlng) {
    const lat = latlng.lat.toFixed(6);
    const lng = latlng.lng.toFixed(6);
    latInput.value = lat;
    lngInput.value = lng;

    if (marker) {
      marker.setLatLng(latlng);
    } else {
      marker = L.marker(latlng, { draggable: true }).addTo(map);
      marker.on("dragend", () => place(marker.getLatLng()));
    }

    status.textContent = `Location set (${lat}, ${lng}). Drag the pin to adjust it.`;
    status.classList.add("picker-status--set");
  }

  if (hasValue) place(L.latLng(start));
  map.on("click", (event) => place(event.latlng));
}