function appendTocList(document, parent, nodes) {
  const list = document.createElement("ol");
  for (const node of nodes) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `#${encodeURIComponent(node.id)}`;
    link.textContent = node.text;
    item.append(link);
    if (node.children.length) {
      appendTocList(document, item, node.children);
    }
    list.append(item);
  }
  parent.append(list);
}

export function mountMarkdownViewer(container, markdown, {
  renderer,
  renderOptions = {},
  ariaLabel = "Markdown 문서",
} = {}) {
  if (!container?.ownerDocument || typeof renderer?.renderWithMetadata !== "function") {
    throw new TypeError("viewer requires a container and Markdown renderer");
  }
  const document = container.ownerDocument;
  const article = document.createElement("article");
  article.className = "markdown-viewer";
  article.setAttribute("aria-label", ariaLabel);
  const toc = document.createElement("nav");
  toc.className = "markdown-toc";
  toc.setAttribute("aria-label", "문서 목차");
  const content = document.createElement("div");
  content.className = "markdown-viewer__content";
  article.append(toc, content);
  container.replaceChildren(article);
  let destroyed = false;
  let activeRenderOptions = renderOptions;

  function update(source, nextRenderOptions = activeRenderOptions) {
    if (destroyed) {
      throw new Error("Markdown viewer has been destroyed");
    }
    activeRenderOptions = nextRenderOptions;
    const result = renderer.renderWithMetadata(source, activeRenderOptions);
    content.innerHTML = result.html;
    toc.replaceChildren();
    if (activeRenderOptions.showToc && result.toc.length) {
      appendTocList(document, toc, result.toc);
      toc.hidden = false;
    } else {
      toc.hidden = true;
    }
    article.classList.toggle(
      "markdown-viewer--compact",
      Boolean(activeRenderOptions.compact),
    );
    if (!source && activeRenderOptions.emptyMessage) {
      const empty = document.createElement("p");
      empty.className = "markdown-viewer__empty";
      empty.textContent = activeRenderOptions.emptyMessage;
      content.replaceChildren(empty);
    }
    return result;
  }

  const result = update(markdown);
  return Object.freeze({
    article,
    destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      article.remove();
    },
    result,
    setRenderOptions(nextRenderOptions) {
      activeRenderOptions = nextRenderOptions;
    },
    update,
  });
}
