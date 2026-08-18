export function mountWebCollectionEditor(container, { preview, formatters = [] } = {}) {
  container.innerHTML = `
    <label>URL<input data-field="url" type="url" maxlength="2048" required placeholder="https://example.com/notices"></label>
    <div class="web-preview-actions"><button type="button" data-action="preview">페이지 미리보기</button><span data-preview-status role="status" aria-live="polite"></span></div>
    <dialog class="web-preview-dialog" data-preview-dialog aria-labelledby="web-preview-title">
      <section class="web-preview-shell">
        <header class="web-preview-header">
          <div><p class="workspace-eyebrow">PAGE PREVIEW</p><h4 id="web-preview-title">수집 영역 선택</h4></div>
          <button type="button" class="jobs-icon-button" data-action="close-preview" aria-label="페이지 미리보기 닫기">×</button>
        </header>
        <div class="web-preview-body">
          <p>수집할 영역을 이미지에서 선택하세요.</p>
          <div class="web-preview-canvas" data-preview-canvas></div>
        </div>
        <footer class="web-preview-footer">
          <span data-preview-dialog-status role="status" aria-live="polite"></span>
          <button type="button" class="jobs-primary" data-action="close-preview">선택 완료</button>
        </footer>
      </section>
    </dialog>
    <label>CSS selector<input data-field="selector" maxlength="512" required placeholder="미리보기에서 영역을 선택하세요"></label>
    <label>Note 정책<select data-field="note_policy"><option value="CHANGED_ONLY">변경 시 생성</option><option value="EVERY_RUN">매 실행 생성</option></select></label>
    <label>Note 정리 방식<select data-field="output_format"><option value="RAW_MARKDOWN">수집 원문 Markdown</option><option value="FINANCIAL_MARKET_BRIEFING">AI 금융시장 브리핑 (기본)</option></select><small data-output-format-help>선택 영역의 Markdown을 그대로 저장합니다.</small></label>`;
  const url = container.querySelector('[data-field="url"]');
  const selector = container.querySelector('[data-field="selector"]');
  const status = container.querySelector("[data-preview-status]");
  const previewButton = container.querySelector('[data-action="preview"]');
  const previewDialog = container.querySelector("[data-preview-dialog]");
  const dialogStatus = container.querySelector("[data-preview-dialog-status]");
  const canvas = container.querySelector("[data-preview-canvas]");
  const outputFormat = container.querySelector('[data-field="output_format"]');
  const outputFormatHelp = container.querySelector("[data-output-format-help]");
  const profilePrefix = "FORMATTER_PROFILE:";
  let formatterProfiles = [...formatters];
  let metadata = {};
  let geometry = {};
  let sequence = 0;
  let destroyed = false;
  let previewInFlight = false;
  const previewMessages = {
    BROWSER_SATURATED: "다른 미리보기나 수집이 진행 중입니다. 잠시 기다린 뒤 다시 시도하세요.",
    BROWSER_TIMEOUT: "페이지가 제한 시간 안에 안정되지 않았습니다.",
    BROWSER_NAVIGATION_FAILED: "페이지 연결이나 렌더링이 중간에 끊겼습니다. 잠시 뒤 다시 시도하세요.",
    BROWSER_CRASHED: "브라우저가 중간에 종료되었습니다. 바로 다시 시도하세요.",
    TRANSFER_LIMIT_EXCEEDED: "페이지가 허용된 전송량을 초과했습니다.",
    SCREENSHOT_TIMEOUT: "페이지 화면을 제한 시간 안에 만들지 못했습니다.",
  };

  function renderOutputFormat() {
    if (outputFormat.value === "RAW_MARKDOWN") {
      outputFormatHelp.textContent = "선택 영역의 Markdown을 그대로 저장합니다.";
    } else if (outputFormat.value === "FINANCIAL_MARKET_BRIEFING") {
      const builtin = formatterProfiles.find((item) => item.is_builtin);
      outputFormatHelp.textContent = `${builtin?.name || "기본 금융시장 브리핑"} 지침으로 정리합니다. Worker에 OPENAI_API_KEY가 필요합니다.`;
    } else {
      const profile = formatterProfiles.find((item) => `${profilePrefix}${item.id}` === outputFormat.value);
      outputFormatHelp.textContent = profile
        ? `${profile.name} · ${profile.model} · 현재 ${profile.use_count || 0}개 Job에서 사용`
        : "선택한 사용자 정리 방식으로 Markdown을 생성합니다.";
    }
  }

  function renderFormatterOptions(selectedValue = outputFormat.value) {
    outputFormat.querySelectorAll("option[data-profile-id]").forEach((option) => option.remove());
    const builtin = formatterProfiles.find((profile) => profile.is_builtin);
    const builtinOption = outputFormat.querySelector('option[value="FINANCIAL_MARKET_BRIEFING"]');
    if (builtinOption) {
      builtinOption.textContent = `${builtin?.name || "AI 금융시장 브리핑"} (기본)`;
    }
    for (const profile of formatterProfiles.filter((item) => !item.is_builtin)) {
      const option = document.createElement("option");
      option.value = `${profilePrefix}${profile.id}`;
      option.dataset.profileId = profile.id;
      option.textContent = `사용자 방식 · ${profile.name}`;
      outputFormat.append(option);
    }
    outputFormat.value = selectedValue;
    if (!outputFormat.value) outputFormat.value = "RAW_MARKDOWN";
    renderOutputFormat();
  }

  outputFormat.addEventListener("change", renderOutputFormat);
  renderFormatterOptions();

  function selectCandidate(candidate, button) {
    selector.value = candidate.selector;
    metadata = { tag: candidate.tag, text: candidate.text };
    geometry = {
      fallback_x: candidate.x,
      fallback_y: candidate.y,
      fallback_width: candidate.width,
      fallback_height: candidate.height,
    };
    canvas.querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    const message = `${candidate.tag} 영역을 선택했습니다.`;
    status.textContent = message;
    dialogStatus.textContent = message;
  }

  container.querySelectorAll('[data-action="close-preview"]').forEach((button) => {
    button.addEventListener("click", () => previewDialog.close());
  });
  previewDialog.addEventListener("close", () => previewButton.focus());

  async function loadPreview() {
    if (previewInFlight || !url.reportValidity() || typeof preview !== "function") return;
    previewInFlight = true;
    const requestSequence = ++sequence;
    const loadingMessage = "페이지를 안전하게 불러오는 중입니다.";
    const originalLabel = previewButton.textContent;
    const loading = document.createElement("div");
    loading.className = "web-preview-loading";
    loading.innerHTML = '<span aria-hidden="true"></span><strong>수집 영역을 준비하는 중입니다.</strong><small>페이지의 값과 배치가 안정될 때까지 잠시 기다려 주세요.</small>';
    status.textContent = loadingMessage;
    dialogStatus.textContent = loadingMessage;
    previewButton.disabled = true;
    previewButton.setAttribute("aria-busy", "true");
    previewButton.textContent = "불러오는 중…";
    canvas.replaceChildren(loading);
    if (!previewDialog.open) previewDialog.showModal();
    try {
      const result = await preview({ url: url.value.trim() });
      if (destroyed || requestSequence !== sequence) return;
      if (!result.success) {
        throw new Error(previewMessages[result.code] || `미리보기를 만들 수 없습니다. (${result.code})`);
      }
      const value = result.preview;
      const image = document.createElement("img");
      image.src = value.screenshot;
      image.alt = `${value.page_title || "웹페이지"} 미리보기`;
      image.width = value.width;
      image.height = value.height;
      const overlay = document.createElement("div");
      overlay.className = "web-preview-overlay";
      for (const candidate of value.candidates) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "web-preview-target";
        button.style.left = `${candidate.x / value.width * 100}%`;
        button.style.top = `${candidate.y / value.height * 100}%`;
        button.style.width = `${candidate.width / value.width * 100}%`;
        button.style.height = `${candidate.height / value.height * 100}%`;
        button.setAttribute("aria-label", `${candidate.tag} ${candidate.text || "영역"}`);
        button.setAttribute("aria-pressed", "false");
        button.addEventListener("click", () => selectCandidate(candidate, button));
        overlay.append(button);
      }
      const stage = document.createElement("div");
      stage.className = "web-preview-stage";
      stage.append(image, overlay);
      canvas.replaceChildren(stage);
      const message = `${value.candidates.length}개의 선택 가능한 영역을 찾았습니다.`;
      status.textContent = message;
      dialogStatus.textContent = message;
    } catch (error) {
      if (!destroyed && requestSequence === sequence) {
        const message = error instanceof Error ? error.message : "미리보기를 만들 수 없습니다.";
        status.textContent = message;
        dialogStatus.textContent = `${message} 바로 다시 시도할 수 있습니다.`;
        const failure = document.createElement("div");
        failure.className = "web-preview-error";
        const failureMessage = document.createElement("p");
        failureMessage.textContent = message;
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "jobs-primary web-preview-retry";
        retry.textContent = "다시 시도";
        retry.addEventListener("click", loadPreview);
        failure.append(failureMessage, retry);
        canvas.replaceChildren(failure);
        requestAnimationFrame(() => retry.focus());
      }
    } finally {
      if (!destroyed && requestSequence === sequence) {
        previewButton.disabled = false;
        previewButton.removeAttribute("aria-busy");
        previewButton.textContent = originalLabel;
      }
      previewInFlight = false;
    }
  }

  previewButton.addEventListener("click", loadPreview);

  return {
    serialize() {
      const formatterProfileId = outputFormat.value.startsWith(profilePrefix)
        ? outputFormat.value.slice(profilePrefix.length)
        : null;
      return {
        url: url.value,
        selector: selector.value,
        note_policy: container.querySelector('[data-field="note_policy"]').value,
        output_format: outputFormat.value === "RAW_MARKDOWN"
          ? "RAW_MARKDOWN"
          : "FINANCIAL_MARKET_BRIEFING",
        formatter_profile_id: formatterProfileId,
        selector_metadata: metadata,
        ...geometry,
        extract_links: true,
        extract_images: false,
      };
    },
    validate() { return container.querySelectorAll(":invalid").length === 0; },
    hydrate(value = {}) {
      for (const key of ["url", "selector", "note_policy"]) {
        const field = container.querySelector(`[data-field="${key}"]`);
        if (field && value[key] != null) field.value = value[key];
      }
      const selectedFormat = value.formatter_profile_id
        ? `${profilePrefix}${value.formatter_profile_id}`
        : value.output_format || "RAW_MARKDOWN";
      renderFormatterOptions(selectedFormat);
      metadata = value.selector_metadata || {};
      geometry = Object.fromEntries(
        ["fallback_x", "fallback_y", "fallback_width", "fallback_height"]
          .filter((key) => value[key] != null).map((key) => [key, value[key]])
      );
      renderOutputFormat();
    },
    setFormatters(nextFormatters = []) {
      formatterProfiles = [...nextFormatters];
      renderFormatterOptions();
    },
    destroy() {
      destroyed = true;
      sequence += 1;
      if (previewDialog.open) previewDialog.close();
      container.replaceChildren();
    },
  };
}
