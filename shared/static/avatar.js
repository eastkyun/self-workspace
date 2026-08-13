const hash = (value) => {
  let result = 2166136261;
  for (const character of value) result = Math.imul(result ^ character.charCodeAt(0), 16777619);
  return result >>> 0;
};

export function avatarSvg(seed, size = 32) {
  const value = hash(seed);
  const hue = value % 360;
  let bits = value;
  let cells = "";
  for (let row = 0; row < 5; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      bits = Math.imul(bits ^ 0x9e3779b9, 1664525) + 1013904223;
      if ((bits >>> 28) % 2 === 0) continue;
      for (const target of column === 2 ? [column] : [column, 4 - column]) {
        cells += `<rect x="${target}" y="${row}" width="1" height="1"/>`;
      }
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 5 5" width="${size}" height="${size}" role="img" aria-label="생성된 프로필 아이콘"><rect width="5" height="5" rx="1" fill="hsl(${hue} 55% 92%)"/><g fill="hsl(${hue} 55% 42%)">${cells}</g></svg>`;
}

export function avatarSeed(userId) {
  const key = `my-workspace:avatar-seed:${userId}`;
  try { return localStorage.getItem(key) || userId; } catch { return userId; }
}

export function setAvatarSeed(userId, seed) {
  try { localStorage.setItem(`my-workspace:avatar-seed:${userId}`, seed); } catch { /* storage unavailable */ }
}

globalThis.workspaceAvatar = {avatarSvg, avatarSeed, setAvatarSeed};
