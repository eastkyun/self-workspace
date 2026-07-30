const root = document.querySelector("#publication-types-app");

if (root) {
  const endpoint = root.dataset.apiEndpoint;
  const list = root.querySelector("#publication-type-list");
  const form = root.querySelector("#publication-type-form");
  const status = root.querySelector("#publication-type-status");
  const fields = {
    id: root.querySelector("#type-id"),
    name: root.querySelector("#type-display-name"),
    order: root.querySelector("#type-order"),
    enabled: root.querySelector("#type-enabled"),
    toc: root.querySelector("#type-toc"),
    related: root.querySelector("#type-related"),
    remove: root.querySelector("#type-delete"),
    reset: root.querySelector("#type-reset"),
  };
  let editingId = null;

  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: options.body ? { "Content-Type": "application/json" } : {},
      ...options,
    });
    if (response.status === 204) return null;
    const payload = await response.json();
    if (!response.ok) {
      const detail = typeof payload.detail === "string"
        ? payload.detail
        : payload.detail?.message;
      throw new Error(detail || "요청을 처리하지 못했습니다.");
    }
    return payload;
  }

  function reset() {
    editingId = null;
    form.reset();
    fields.enabled.checked = true;
    fields.id.disabled = false;
    fields.remove.hidden = true;
    status.textContent = "";
    fields.id.focus();
  }

  function edit(item) {
    if (item.builtin) return;
    status.textContent = "";
    editingId = item.type_id;
    fields.id.value = item.type_id;
    fields.name.value = item.display_name;
    fields.order.value = item.order;
    fields.enabled.checked = item.enabled;
    fields.toc.checked = item.render_options.toc_enabled;
    fields.related.checked = item.render_options.related_enabled;
    fields.id.disabled = true;
    fields.remove.hidden = false;
  }

  function render(items) {
    list.replaceChildren(...items.map((item) => {
      const card = document.createElement("article");
      card.className = "publication-type-card";
      const title = document.createElement("strong");
      title.textContent = item.display_name;
      const metadata = document.createElement("span");
      metadata.textContent = [
        item.type_id,
        item.route_prefix,
        item.enabled ? "enabled" : "disabled",
        item.restart_required ? "restart required" : "active",
      ].join(" · ");
      const action = document.createElement("button");
      action.type = "button";
      action.textContent = item.builtin ? "기본 Provider" : "수정";
      action.disabled = item.builtin;
      action.addEventListener("click", () => edit(item));
      card.append(title, metadata, action);
      return card;
    }));
  }

  async function load() {
    render(await request(endpoint));
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      type_id: fields.id.value,
      display_name: fields.name.value,
      order: Number(fields.order.value),
      enabled: fields.enabled.checked,
      toc_enabled: fields.toc.checked,
      related_enabled: fields.related.checked,
      version_visible: true,
    };
    try {
      const saved = await request(
        editingId ? `${endpoint}/${editingId}` : endpoint,
        {
          method: editingId ? "PUT" : "POST",
          body: JSON.stringify(payload),
        },
      );
      status.textContent = `${saved.display_name} 저장 완료 · 적용하려면 Workspace를 재시작하세요.`;
      await load();
    } catch (error) {
      status.textContent = error.message;
    }
  });

  fields.remove.addEventListener("click", async () => {
    if (!editingId || !window.confirm("이 Publication Type을 삭제할까요?")) return;
    try {
      await request(`${endpoint}/${editingId}`, { method: "DELETE" });
      reset();
      status.textContent = "삭제했습니다. 적용하려면 Workspace를 재시작하세요.";
      await load();
    } catch (error) {
      status.textContent = error.message;
    }
  });
  fields.reset.addEventListener("click", reset);
  load().catch((error) => { status.textContent = error.message; });
}
