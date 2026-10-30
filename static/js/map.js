const BUCHAREST_CENTER = [44.4268, 26.1025];

document.addEventListener("DOMContentLoaded", () => {
  const mapElement = document.getElementById("map");
  if (!mapElement) return;

  const map = L.map(mapElement).setView(BUCHAREST_CENTER, 12);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  loadPitches(map, mapElement.dataset.apiUrl);
});

async function loadPitches(map, apiUrl) {
  try {
    const response = await fetch(apiUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    if (data.pitches.length === 0) {
      document.getElementById("map-empty").classList.remove("d-none");
      return;
    }

    const markers = data.pitches.map((pitch) =>
      L.marker([pitch.lat, pitch.lng]).bindPopup(buildPopup(pitch)).addTo(map)
    );

    // Zoom the map so all pitches are visible
    const group = L.featureGroup(markers);
    map.fitBounds(group.getBounds().pad(0.2), { maxZoom: 14 });
  } catch (error) {
    console.error("Could not load pitches:", error);
  }
}

// Builds DOM elements with textContent (not innerHTML),
// so names typed by users can never inject HTML or scripts.
function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function buildPopup(pitch) {
  const container = createElement("div");
  container.appendChild(createElement("h6", "mb-1 fw-bold", pitch.name));
  container.appendChild(createElement("div", "text-muted small", `${pitch.address} · ${pitch.sector}`));

  pitch.matches.forEach((match) => {
    const item = createElement("div", "border-top pt-2 mt-2");
    item.appendChild(createElement("div", "fw-semibold", match.start));

    const hasSpots = match.spots_left > 0;
    const spotsText = hasSpots ? `${match.spots_left}/${match.spots_needed} spots left` : "Full";
    const priceText = Number(match.price) === 0 ? "Free" : `${match.price} RON`;
    item.appendChild(
      createElement("div", `small ${hasSpots ? "text-success" : "text-secondary"}`, `${spotsText} · ${priceText}`)
    );

    const link = createElement("a", "btn btn-sm btn-success text-white mt-1", "View match");
    link.href = match.url;
    item.appendChild(link);

    container.appendChild(item);
  });

  return container;
}