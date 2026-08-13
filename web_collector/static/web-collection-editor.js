export function mountWebCollectionEditor(container) {
  container.innerHTML = `
    <label>URL<input data-field="url" type="url" maxlength="2048" required placeholder="https://example.com/notices"></label>
    <label>CSS selector<input data-field="selector" maxlength="512" required placeholder="#notice-list"></label>
    <label>Note 정책<select data-field="note_policy"><option value="CHANGED_ONLY">변경 시 생성</option><option value="EVERY_RUN">매 실행 생성</option></select></label>`;
  return {
    serialize() {
      return {
        url: container.querySelector('[data-field="url"]').value,
        selector: container.querySelector('[data-field="selector"]').value,
        note_policy: container.querySelector('[data-field="note_policy"]').value,
        selector_metadata: {}, extract_links: true, extract_images: false,
      };
    },
    validate() { return container.querySelectorAll(":invalid").length === 0; },
    hydrate(value = {}) {
      for (const key of ["url", "selector", "note_policy"]) {
        const field = container.querySelector(`[data-field="${key}"]`);
        if (field && value[key] != null) field.value = value[key];
      }
    },
    destroy() { container.replaceChildren(); },
  };
}
