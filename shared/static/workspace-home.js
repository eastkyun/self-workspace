const root = document.querySelector("[data-workspace-home]");

if (root) {
  const hour = new Date().getHours();
  const greeting = hour < 6 ? "늦은 시간에도 함께할게요." : hour < 12 ? "좋은 아침이에요." : hour < 18 ? "좋은 오후예요." : "편안한 저녁이에요.";
  const target = root.querySelector("[data-home-greeting]");
  if (target) target.textContent = greeting;

  const relative = new Intl.RelativeTimeFormat("ko", {numeric: "auto"});
  const units = [["year", 31536000000], ["month", 2592000000], ["day", 86400000], ["hour", 3600000], ["minute", 60000]];
  for (const time of root.querySelectorAll("[data-relative-time]")) {
    const value = new Date(time.dateTime);
    if (Number.isNaN(value.getTime())) continue;
    const delta = value.getTime() - Date.now();
    const [unit, size] = units.find(([, threshold]) => Math.abs(delta) >= threshold) || ["minute", 60000];
    time.textContent = Math.abs(delta) < 60000 ? "방금" : relative.format(Math.round(delta / size), unit);
    time.title = value.toLocaleString("ko-KR");
  }

  const dashboard = root.querySelector("[data-dashboard]");
  if (dashboard) {
    const dialog = dashboard.querySelector("[data-dashboard-dialog]");
    const status = dashboard.querySelector("[data-dashboard-status]");
    const layoutRows = () => [...dialog.querySelectorAll("[data-layout-key]")];
    const payload = () => ({items: layoutRows().map((row, index) => ({
      widget_key: row.dataset.layoutKey,
      display_order: index,
      size: row.dataset.layoutSize,
      settings: {},
      enabled: row.dataset.layoutEnabled === "true",
    }))});
    const csrf = () => {
      const item = document.cookie.split("; ").find((entry) => entry.split("=", 1)[0].endsWith("_csrf"));
      return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
    };
    const save = async () => {
      status.textContent = "저장 중…";
      const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(dashboard.dataset.workspaceId)}/dashboard/layout`, {
        method: "PUT",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()},
        body: JSON.stringify(payload()),
      });
      if (!response.ok) throw new Error("dashboard layout update failed");
      status.textContent = "저장했습니다.";
    };
    const saveAndReload = async () => {
      try { await save(); window.location.reload(); }
      catch { status.textContent = "저장하지 못했습니다."; }
    };
    const grid = dashboard.querySelector("[data-dashboard-grid]");
    const layoutRow = (key) => layoutRows().find((item) => item.dataset.layoutKey === key);
    const syncLayoutOrderFromGrid = () => {
      for (const card of grid.querySelectorAll("[data-widget-key]")) {
        const row = layoutRow(card.dataset.widgetKey);
        if (row) row.parentNode.append(row);
      }
    };
    dashboard.querySelector("[data-dashboard-manage]")?.addEventListener("click", () => dialog.showModal());
    dialog.addEventListener("click", async (event) => {
      const row = event.target.closest("[data-layout-key]");
      if (!row) return;
      const toggle = event.target.closest("[data-widget-toggle]");
      const move = event.target.closest("[data-layout-move]");
      if (toggle) {
        row.dataset.layoutEnabled = row.dataset.layoutEnabled === "true" ? "false" : "true";
        toggle.textContent = row.dataset.layoutEnabled === "true" ? "숨기기" : "표시하기";
      } else if (move) {
        const rows = layoutRows();
        const index = rows.indexOf(row);
        const target = move.dataset.layoutMove === "up" ? rows[index - 1] : rows[index + 1];
        if (!target) return;
        move.dataset.layoutMove === "up"
          ? row.parentNode.insertBefore(row, target)
          : row.parentNode.insertBefore(target, row);
      } else return;
      await saveAndReload();
    });
    dialog.addEventListener("change", async (event) => {
      const select = event.target.closest("[data-layout-size]");
      if (!select) return;
      select.closest("[data-layout-key]").dataset.layoutSize = select.value;
      await saveAndReload();
    });
    grid?.addEventListener("click", async (event) => {
      const card = event.target.closest("[data-widget-key]");
      if (!card) return;
      const row = layoutRow(card.dataset.widgetKey);
      const hide = event.target.closest("[data-widget-hide]");
      if (hide && row) row.dataset.layoutEnabled = "false";
      if (!hide) return;
      await saveAndReload();
    });

    let nativeDrag = null;
    grid?.addEventListener("dragstart", (event) => {
      const card = event.target.closest("[data-widget-drag-handle]")?.closest("[data-widget-key]");
      if (!card) return;
      nativeDrag = card;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", card.dataset.widgetKey);
    });
    grid?.addEventListener("dragover", (event) => {
      if (!nativeDrag || !event.target.closest("[data-widget-key]")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    });
    grid?.addEventListener("drop", async (event) => {
      const target = event.target.closest("[data-widget-key]");
      if (!nativeDrag || !target || target === nativeDrag) return;
      event.preventDefault();
      const box = target.getBoundingClientRect();
      const after = event.clientY > box.top + box.height / 2 || event.clientX > box.left + box.width / 2;
      grid.insertBefore(nativeDrag, after ? target.nextSibling : target);
      syncLayoutOrderFromGrid();
      await saveAndReload();
    });
    grid?.addEventListener("dragend", () => {
      nativeDrag?.classList.remove("is-dragging");
      nativeDrag = null;
    });

    let pointerDrag = null;
    grid?.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest("[data-widget-drag-handle]");
      const card = handle?.closest("[data-widget-key]");
      if (!card) return;
      pointerDrag = {handle, card};
      handle.setPointerCapture(event.pointerId);
      card.classList.add("is-dragging");
    });
    grid?.addEventListener("pointermove", (event) => {
      if (!pointerDrag) return;
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-widget-key]");
      if (!target || target === pointerDrag.card || target.parentNode !== grid) return;
      const box = target.getBoundingClientRect();
      const after = event.clientY > box.top + box.height / 2 || event.clientX > box.left + box.width / 2;
      grid.insertBefore(pointerDrag.card, after ? target.nextSibling : target);
    });
    grid?.addEventListener("pointerup", async (event) => {
      if (!pointerDrag) return;
      pointerDrag.handle.releasePointerCapture(event.pointerId);
      pointerDrag.card.classList.remove("is-dragging");
      pointerDrag = null;
      syncLayoutOrderFromGrid();
      await saveAndReload();
    });

    let resize = null;
    grid?.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest("[data-widget-resize]");
      const card = handle?.closest("[data-widget-key]");
      if (!card) return;
      const row = layoutRow(card.dataset.widgetKey);
      resize = {handle, card, row, startX: event.clientX, startSize: row.dataset.layoutSize};
      handle.setPointerCapture(event.pointerId);
      card.classList.add("is-resizing");
    });
    grid?.addEventListener("pointermove", (event) => {
      if (!resize) return;
      const sizes = ["SMALL", "MEDIUM", "LARGE"];
      const start = sizes.indexOf(resize.startSize);
      const step = Math.round((event.clientX - resize.startX) / 90);
      const size = sizes[Math.max(0, Math.min(sizes.length - 1, start + step))];
      resize.row.dataset.layoutSize = size;
      resize.card.className = resize.card.className.replace(/workspace-dashboard-widget--(?:small|medium|large)/, `workspace-dashboard-widget--${size.toLowerCase()}`);
    });
    grid?.addEventListener("pointerup", async (event) => {
      if (!resize) return;
      resize.handle.releasePointerCapture(event.pointerId);
      resize.card.classList.remove("is-resizing");
      resize = null;
      await saveAndReload();
    });
  }
}
