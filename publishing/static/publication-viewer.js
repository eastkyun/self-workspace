import {
  createMarkdownRenderer,
  mountMarkdownViewer,
} from "../shared/markdown/index.js";

const sourceElement = document.querySelector("#publication-source");
const container = document.querySelector("#publication-markdown");

if (sourceElement && container) {
  const payload = JSON.parse(sourceElement.textContent || "{}");
  mountMarkdownViewer(container, payload.markdown || "", {
    renderer: createMarkdownRenderer(),
    renderOptions: payload.renderOptions || {},
    ariaLabel: "발행 문서 본문",
  });
}
