(() => {
  const root = document.querySelector("[data-received-invitations]");
  const cookieName = document.body.dataset.sessionCookie || "my_workspace_session";
  const csrf = () => decodeURIComponent((document.cookie.split("; ").find((item) => item.startsWith(`${cookieName}_csrf=`)) || "=").split("=").slice(1).join("="));
  const load = async () => {
    const response = await fetch("/api/v1/invitations", {headers: {Accept: "application/json"}});
    const invitations = await response.json();
    root.replaceChildren();
    document.querySelector("[data-invitation-count]").textContent = invitations.length ? `${invitations.length}개` : "";
    if (!invitations.length) { root.innerHTML = '<div class="workspace-invitations-empty"><div class="workspace-invitations-empty-icon">✦</div><h3>새로운 초대가 없습니다</h3><p>workspace 초대를 받으면 이곳에서 확인할 수 있습니다.</p></div>'; return; }
    for (const invitation of invitations) {
      const card = document.createElement("article");
      card.className = "workspace-invitation-card";
      const title = document.createElement("h3"); title.textContent = invitation.name;
      const detail = document.createElement("p"); detail.textContent = `${invitation.role_key} 권한 · ${new Date(invitation.expires_at).toLocaleDateString("ko-KR")}까지`;
      const accept = document.createElement("button"); accept.dataset.action = "accept"; accept.textContent = "참여하기";
      const reject = document.createElement("button"); reject.dataset.action = "reject"; reject.textContent = "거절";
      const status = document.createElement("p"); status.setAttribute("role", "status");
      const copy = document.createElement("div"); copy.className = "workspace-invitation-card-copy"; copy.append(title, detail);
      const actions = document.createElement("div"); actions.className = "workspace-invitation-card-actions"; actions.append(accept, reject);
      card.append(copy, actions, status);
      card.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-action]"); if (!button) return;
        button.disabled = true;
        const response = await fetch(`/api/v1/invitations/received/${encodeURIComponent(invitation.id)}/${button.dataset.action}`, {method: "POST", headers: {"X-CSRF-Token": csrf()}});
        if (response.ok && button.dataset.action === "accept") { const body = await response.json(); location.assign(`/w/${encodeURIComponent(body.workspace_id)}/home`); return; }
        if (response.ok) { card.remove(); return; }
        card.querySelector("[role=status]").textContent = "처리하지 못했습니다. 다시 시도해 주세요."; button.disabled = false;
      });
      root.append(card);
    }
  };
  load().catch(() => { root.textContent = "초대 목록을 불러오지 못했습니다."; });
})();
