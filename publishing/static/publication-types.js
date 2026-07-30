const root = document.querySelector("#publication-types-app");

if (root) {
  const endpoint = root.dataset.apiEndpoint;
  const list = root.querySelector("#publication-type-list");
  const count = root.querySelector("#publication-type-count");
  const form = root.querySelector("#publication-type-form");
  const formTitle = root.querySelector("#publication-type-form-title");
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
    submit: root.querySelector("#type-submit"),
  };
  let editingId = null;
  let currentItems = [];

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

  function setStatus(message = "", tone = "") {
    status.textContent = message;
    status.classList.toggle("is-success", tone === "success");
    status.classList.toggle("is-error", tone === "error");
  }

  function setBusy(busy) {
    fields.submit.disabled = busy;
    fields.remove.disabled = busy;
    fields.reset.disabled = busy;
    fields.submit.textContent = busy ? "저장 중…" : "유형 저장";
  }

  function selectCard() {
    list.querySelectorAll(".publication-type-card").forEach((card) => {
      const selected = card.dataset.typeId === editingId;
      card.classList.toggle("is-selected", selected);
      if (selected) card.setAttribute("aria-current", "true");
      else card.removeAttribute("aria-current");
    });
  }

  function reset({ focus = true, clearStatus = true } = {}) {
    editingId = null;
    form.reset();
    fields.enabled.checked = true;
    fields.id.disabled = false;
    fields.remove.hidden = true;
    formTitle.textContent = "새 유형 등록";
    fields.submit.textContent = "유형 저장";
    if (clearStatus) setStatus();
    selectCard();
    if (focus) fields.id.focus();
  }

  function edit(item) {
    if (item.builtin) return;
    setStatus();
    editingId = item.type_id;
    fields.id.value = item.type_id;
    fields.name.value = item.display_name;
    fields.order.value = item.order;
    fields.enabled.checked = item.enabled;
    fields.toc.checked = item.render_options.toc_enabled;
    fields.related.checked = item.render_options.related_enabled;
    fields.id.disabled = true;
    fields.remove.hidden = false;
    formTitle.textContent = `${item.display_name} 수정`;
    selectCard();
    fields.name.focus();
  }

  function badge(label, className) {
    const element = document.createElement("span");
    element.className = `publication-type-badge ${className}`;
    element.textContent = label;
    return element;
  }

  function render(items) {
    currentItems = items;
    count.textContent = `${items.length} types`;
    list.replaceChildren(...items.map((item) => {
      const card = document.createElement("article");
      card.className = `publication-type-card ${
        item.builtin ? "is-builtin" : "is-custom"
      }`;
      card.dataset.typeId = item.type_id;

      const heading = document.createElement("div");
      heading.className = "publication-type-card-heading";
      const mark = document.createElement("span");
      mark.className = "publication-type-card-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = item.display_name.slice(0, 1).toUpperCase();
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = item.display_name;
      const route = document.createElement("code");
      route.textContent = `${item.type_id} · ${item.route_prefix}`;
      copy.append(title, route);
      heading.append(mark, copy);

      const badges = document.createElement("div");
      badges.className = "publication-type-card-badges";
      badges.append(
        badge(item.builtin ? "Built-in" : "Custom", item.builtin ? "is-builtin" : "is-custom"),
        badge(item.enabled ? "Enabled" : "Disabled", item.enabled ? "is-enabled" : "is-disabled"),
      );
      if (item.restart_required) {
        badges.append(badge("Restart required", "is-restart"));
      }

      const options = document.createElement("p");
      const features = [
        item.render_options.toc_enabled ? "목차" : null,
        item.render_options.related_enabled ? "관련 문서" : null,
        item.render_options.version_visible ? "버전 표시" : null,
      ].filter(Boolean);
      options.textContent = features.length
        ? features.join(" · ")
        : "기본 viewer 옵션";

      const action = document.createElement(item.builtin ? "span" : "button");
      action.className = "publication-type-card-action";
      if (item.builtin) {
        action.textContent = "보호됨";
        action.setAttribute("aria-label", `${item.display_name} 기본 Provider는 보호됨`);
      } else {
        action.type = "button";
        action.textContent = "수정";
        action.setAttribute("aria-label", `${item.display_name} 수정`);
        action.addEventListener("click", () => edit(item));
      }

      card.append(heading, badges, options, action);
      return card;
    }));
    selectCard();
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
    setBusy(true);
    setStatus("설정을 저장하고 있습니다…");
    try {
      const saved = await request(
        editingId ? `${endpoint}/${editingId}` : endpoint,
        {
          method: editingId ? "PUT" : "POST",
          body: JSON.stringify(payload),
        },
      );
      editingId = saved.type_id;
      await load();
      const savedItem = currentItems.find((item) => item.type_id === editingId);
      if (savedItem) edit(savedItem);
      setStatus(
        `${saved.display_name} 저장 완료 · 발행 대상에 즉시 적용했습니다.`,
        "success",
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  });

  fields.remove.addEventListener("click", async () => {
    if (!editingId || !window.confirm("이 Publication Type을 삭제할까요?")) return;
    setBusy(true);
    setStatus("유형을 삭제하고 있습니다…");
    try {
      await request(`${endpoint}/${editingId}`, { method: "DELETE" });
      reset({ focus: false, clearStatus: false });
      await load();
      setStatus(
        "유형을 삭제하고 Registry에 즉시 반영했습니다.",
        "success",
      );
      fields.id.focus();
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  });
  fields.reset.addEventListener("click", () => reset());
  load().catch((error) => setStatus(error.message, "error"));
}
