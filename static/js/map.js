const BUCHAREST_CENTER = [44.4268, 26.1025];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const DAY_FILTERS = ["today", "tomorrow", "week", "next-week", "all"];
const DEFAULT_FILTERS = { day: "all", sector: "", open: false };

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

/* ---------- Home page: filters, map and list ---------- */

async function initHomeMap(element) {
  const map = createBaseMap(element, BUCHAREST_CENTER, 12);
  const markerLayer = L.layerGroup().addTo(map);
  const list = document.getElementById("match-list");
  const summary = document.getElementById("match-summary");
  const form = document.getElementById("filters");
  const clearButton = document.getElementById("clear-filters");

  let data;
  try {
    const response = await fetch(element.dataset.apiUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    console.error("Could not load pitches:", error);
    summary.textContent = "Matches could not be loaded. Refresh the page to try again.";
    form.hidden = true;
    list.replaceChildren();
    return;
  }

  // Every match together with its pitch, sorted by kick-off
  const allMatches = data.pitches
    .flatMap((pitch) => pitch.matches.map((match) => ({ ...match, pitch })))
    .sort((a, b) => a.start_iso.localeCompare(b.start_iso));

  if (allMatches.length === 0) {
    summary.textContent = "No upcoming matches yet.";
    form.hidden = true;
    list.replaceChildren(el("p", "list-message", "When an organizer schedules a match, it will show up here."));
    return;
  }

  let filters = readFiltersFromUrl();
  let markers = new Map();
  writeFiltersToForm(form, filters);

  function render(animate) {
    const visible = allMatches.filter((match) => matchesFilters(match, filters));
    const filtered = isFiltered(filters);

    renderSummary(summary, visible, allMatches.length, filtered);
    renderList(list, visible, (match) => focusPitch(map, element, markers.get(match.pitch.id)));
    markers = renderMarkers(markerLayer, visible);
    clearButton.hidden = !filtered;

    // Move the map so all remaining pitches are in view
    if (markers.size > 0) {
      const bounds = L.featureGroup([...markers.values()]).getBounds().pad(0.2);
      map.fitBounds(bounds, { maxZoom: 14, animate: animate && !REDUCE_MOTION });
    }
  }

  function update(newFilters) {
    filters = newFilters;
    writeFiltersToUrl(filters);
    render(true);
  }

  form.addEventListener("change", () => update(readFiltersFromForm(form)));
  form.addEventListener("submit", (event) => event.preventDefault());
  clearButton.addEventListener("click", () => {
    writeFiltersToForm(form, DEFAULT_FILTERS);
    update({ ...DEFAULT_FILTERS });
  });

  render(false);
}

/* ---------- Filters ---------- */
function matchesFilters(match, filters) {
  if (filters.day === "today" && !match.is_today) return false;
  if (filters.day === "tomorrow" && !match.is_tomorrow) return false;
  if (filters.day === "week" && !match.is_this_week) return false;
  if (filters.day === "next-week" && !match.is_next_week) return false;
  if (filters.sector && String(match.pitch.sector_id) !== filters.sector) return false;
  if (filters.open && match.spots_left === 0) return false;
  return true;
}

function isFiltered(filters) {
  return filters.day !== "all" || filters.sector !== "" || filters.open;
}

function readFiltersFromForm(form) {
  return {
    day: form.elements.day.value || "all",
    sector: form.elements.sector.value,
    open: form.elements.open.checked,
  };
}

function writeFiltersToForm(form, filters) {
  form.elements.day.value = filters.day;
  form.elements.sector.value = filters.sector;
  form.elements.open.checked = filters.open;
}

// Filters live in the URL (e.g. /?day=weekend&sector=3),
// so a filtered view survives a refresh and can be shared as a link.
function readFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const day = params.get("day");
  const sector = params.get("sector") || "";
  return {
    day: DAY_FILTERS.includes(day) ? day : "all",
    sector: /^[1-7]$/.test(sector) ? sector : "",
    open: params.get("open") === "1",
  };
}

function writeFiltersToUrl(filters) {
  const params = new URLSearchParams();
  if (filters.day !== "all") params.set("day", filters.day);
  if (filters.sector) params.set("sector", filters.sector);
  if (filters.open) params.set("open", "1");
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

/* ---------- Rendering ---------- */

function renderSummary(summary, visible, total, filtered) {
  const openSpots = visible.reduce((sum, match) => sum + match.spots_left, 0);
  const matchWord = total === 1 ? "match" : "matches";
  const spotWord = openSpots === 1 ? "spot" : "spots";
  summary.textContent = filtered
    ? `${visible.length} of ${total} upcoming ${matchWord}, ${openSpots} open ${spotWord}`
    : `${total} upcoming ${matchWord}, ${openSpots} open ${spotWord}`;
}

function renderList(list, matches, onSelect) {
  list.replaceChildren();

  if (matches.length === 0) {
    list.appendChild(el("p", "list-message", "No matches fit these filters. Try another day or sector."));
    return;
  }

  let currentDay = null;
  matches.forEach((match) => {
    if (match.day !== currentDay) {
      currentDay = match.day;
      list.appendChild(el("h2", "list-day", match.day));
    }
    list.appendChild(buildListItem(match, () => onSelect(match)));
  });
}

// One marker per pitch, counting only the matches that pass the filters
function renderMarkers(layer, matches) {
  layer.clearLayers();

  const byPitch = new Map();
  matches.forEach((match) => {
    if (!byPitch.has(match.pitch.id)) byPitch.set(match.pitch.id, { pitch: match.pitch, matches: [] });
    byPitch.get(match.pitch.id).matches.push(match);
  });

  const markers = new Map();
  byPitch.forEach(({ pitch, matches: pitchMatches }) => {
    const openSpots = pitchMatches.reduce((sum, match) => sum + match.spots_left, 0);
    const marker = L.marker([pitch.lat, pitch.lng], { icon: pitchIcon(openSpots), title: pitch.name })
      .bindPopup(buildPopup(pitch, pitchMatches), { maxWidth: 300 });
    layer.addLayer(marker);
    markers.set(pitch.id, marker);
  });

  return markers;
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
  if (!marker) return;

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

function buildPopup(pitch, matches) {
  const box = el("div");
  box.appendChild(el("h3", "popup__title", pitch.name));
  box.appendChild(el("p", "popup__address", `${pitch.address}, ${pitch.sector}`));

  matches.forEach((match) => {
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
  const hasSpots = element.dataset.spotsLeft !== undefined;
  const options = hasSpots ? { icon: pitchIcon(Number(element.dataset.spotsLeft)) } : {};
  L.marker(position, options).addTo(map);
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
