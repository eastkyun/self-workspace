import { decodePinnedIds } from "./hub-state.js";

const STORAGE_KEY = "my-workspace:pinned-apps:v1";
const cardTemplate = document.querySelector("#app-card-template");
const pinnedContainer = document.querySelector("#pinned-apps");
const pinnedEmpty = document.querySelector("#pinned-empty");
const allAppsContainer = document.querySelector("#all-apps");

let apps = [];
let pinnedIds = new Set();

function safePinnedIds(registeredApps) {
  try {
    return new Set(decodePinnedIds(localStorage.getItem(STORAGE_KEY), registeredApps));
  } catch {
    return new Set(decodePinnedIds(null, registeredApps));
  }
}

function savePinnedIds() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...pinnedIds]));
  } catch {
    // The current page remains usable when storage is unavailable.
  }
}

function linkAttributes(link, app) {
  link.href = app.url;
  if (app.kind === "external") {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
}

function appCard(app) {
  const fragment = cardTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".hub-app-card");
  const link = fragment.querySelector(".hub-app-card-link");
  const icon = fragment.querySelector(".hub-app-icon");
  const pinButton = fragment.querySelector(".hub-pin-button");

  card.dataset.appId = app.id;
  linkAttributes(link, app);
  icon.src = app.icon;
  fragment.querySelector(".hub-app-name").textContent = app.name;
  fragment.querySelector(".hub-app-description").textContent = app.description;
  pinButton.textContent = pinnedIds.has(app.id) ? "고정 해제" : "고정";
  pinButton.setAttribute("aria-pressed", String(pinnedIds.has(app.id)));
  pinButton.setAttribute("aria-label", `${app.name} ${pinButton.textContent}`);
  pinButton.addEventListener("click", () => togglePin(app.id));
  return fragment;
}

function render() {
  allAppsContainer.replaceChildren(...apps.map(appCard));
  const pinnedApps = apps.filter((app) => pinnedIds.has(app.id));
  pinnedContainer.replaceChildren(...pinnedApps.map(appCard));
  pinnedEmpty.hidden = pinnedApps.length > 0;
}

function togglePin(appId) {
  if (pinnedIds.has(appId)) pinnedIds.delete(appId);
  else pinnedIds.add(appId);
  savePinnedIds();
  render();
  window.dispatchEvent(new CustomEvent("workspace:pins-sync", {
    detail: { pinnedIds: [...pinnedIds] },
  }));
}

window.addEventListener("workspace:pins-changed", (event) => {
  pinnedIds = new Set(event.detail?.pinnedIds ?? []);
  render();
});

async function start() {
  const response = await fetch("./api/hub/apps", {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("앱 목록을 불러오지 못했습니다.");
  apps = await response.json();
  pinnedIds = safePinnedIds(apps);
  render();
}

start().catch((error) => {
  pinnedEmpty.hidden = false;
  pinnedEmpty.textContent = error.message;
});
