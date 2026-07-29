export function buildToc(headings, {
  minLevel = 2,
  maxLevel = 4,
} = {}) {
  if (!Number.isInteger(minLevel)
      || !Number.isInteger(maxLevel)
      || minLevel < 1
      || maxLevel > 6
      || minLevel > maxLevel) {
    throw new TypeError("invalid TOC heading range");
  }

  const root = [];
  const stack = [{ level: minLevel - 1, children: root }];

  for (const heading of headings ?? []) {
    if (heading.level < minLevel || heading.level > maxLevel) {
      continue;
    }
    const node = {
      id: heading.id,
      level: heading.level,
      text: heading.text,
      children: [],
    };

    while (stack.length > 1 && stack.at(-1).level >= node.level) {
      stack.pop();
    }
    stack.at(-1).children.push(node);
    stack.push(node);
  }

  return root;
}

