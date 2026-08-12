(() => {
  const cookieName = document.body.dataset.sessionCookie || "my_workspace_session";
  const csrf = () => decodeURIComponent(
    (document.cookie.split("; ").find((item) => item.startsWith(`${cookieName}_csrf=`)) || "=")
      .split("=")
      .slice(1)
      .join("="),
  );

  const send = async (url, method, payload) => {
    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()},
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    if (!response.ok) throw new Error("요청을 처리하지 못했습니다. 다시 시도해 주세요.");
    return response;
  };

  const setStatus = (status, message, kind) => {
    if (!status) return;
    status.textContent = message;
    status.classList.remove("is-success", "is-error", "is-pending");
    if (kind) status.classList.add(`is-${kind}`);
  };

  document.querySelector("[data-workspace-selector]")?.addEventListener("change", async (event) => {
    const selector = event.currentTarget;
    const id = selector.value;
    selector.disabled = true;
    try {
      await send(`/api/v1/workspaces/${encodeURIComponent(id)}/select`, "POST");
      window.location.assign(`/w/${encodeURIComponent(id)}/home`);
    } catch (_) {
      window.location.reload();
    }
  });

  document.querySelector("[data-workspace-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (form.dataset.submitting === "true") return;
    const status = form.querySelector("[data-status]");
    const button = form.querySelector("[data-save-button]");
    const buttonLabel = button?.textContent;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    setStatus(status, "변경사항을 저장하고 있습니다…", "pending");
    if (button) {
      button.disabled = true;
      button.textContent = "저장 중…";
    }
    try {
      await send(
        `/api/v1/workspaces/${encodeURIComponent(form.dataset.workspaceId)}`,
        "PATCH",
        Object.fromEntries(new FormData(form)),
      );
      setStatus(status, "변경사항을 저장했습니다.", "success");
    } catch (error) {
      setStatus(status, error.message, "error");
    } finally {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      if (button) {
        button.disabled = false;
        button.textContent = buttonLabel || "변경사항 저장";
      }
    }
  });

  document.querySelector("[data-invitation-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (form.dataset.submitting === "true") return;
    const status = form.querySelector("[data-status]");
    const button = form.querySelector('button[type="submit"], button:not([type])');
    const buttonLabel = button?.textContent;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    setStatus(status, "초대 중…", "pending");
    if (button) {
      button.disabled = true;
      button.textContent = "초대 중…";
    }
    try {
      const response = await send(
        `/api/v1/workspaces/${encodeURIComponent(form.dataset.workspaceId)}/invitations`,
        "POST",
        Object.fromEntries(new FormData(form)),
      );
      const result = await response.json();
      if (result.delivery_status === "SENT") {
        setStatus(status, "초대를 전송했습니다.", "success");
      } else {
        setStatus(status, "초대가 생성되었지만 전송하지 못했습니다. 워크스페이스 설정에서 다시 시도해 주세요.", "error");
      }
      form.reset();
    } catch (error) {
      setStatus(status, error.message, "error");
    } finally {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      if (button) {
        button.disabled = false;
        button.textContent = buttonLabel || "구성원 초대";
      }
    }
  });

  const actions = document.querySelector("[data-invitation-actions]");
  actions?.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-action]");
    if (!trigger || trigger.disabled) return;
    const status = actions.querySelector("[data-status]");
    trigger.disabled = true;
    try {
      const response = await send(
        `/api/v1/invitations/${encodeURIComponent(actions.dataset.token)}/${trigger.dataset.action}`,
        "POST",
      );
      const body = response.status === 201 ? await response.json() : null;
      window.location.assign(body ? `/w/${encodeURIComponent(body.workspace_id)}/settings/general` : "/workspace-selector");
    } catch (error) {
      setStatus(status, error.message, "error");
      trigger.disabled = false;
    }
  });

  document.querySelector("[data-permission-matrix]")?.addEventListener("change", async (event) => {
    const select = event.target.closest("[data-permission-override]");
    if (!select || select.dataset.saving === "true") return;
    const previous = select.dataset.previous || "";
    const workspace = encodeURIComponent(select.dataset.workspaceId);
    const member = encodeURIComponent(select.dataset.memberId);
    const permission = encodeURIComponent(select.dataset.permissionCode);
    const status = document.querySelector("[data-permission-status]");
    select.dataset.saving = "true";
    select.disabled = true;
    setStatus(status, "권한 재정의를 저장하고 있습니다…", "pending");
    try {
      const url = `/api/v1/workspaces/${workspace}/members/${member}/permissions/${permission}`;
      await send(url, select.value ? "PUT" : "DELETE", select.value ? {effect: select.value} : undefined);
      select.dataset.previous = select.value;
      setStatus(status, "권한 재정의를 저장했습니다.", "success");
    } catch (error) {
      select.value = previous;
      setStatus(status, error.message, "error");
    } finally {
      delete select.dataset.saving;
      select.disabled = false;
    }
  });
})();
