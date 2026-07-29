import { getCommand } from "./commands.js";
import { getEditorProfile } from "./profiles.js";

export function createMarkdownToolbar({
  document,
  profile: profileId = "full",
  execute,
  ariaLabel = "Markdown 서식",
} = {}) {
  if (!document || typeof execute !== "function") {
    throw new TypeError("toolbar requires a document and execute callback");
  }
  const profile = getEditorProfile(profileId);
  const toolbar = document.createElement("div");
  toolbar.className = "markdown-toolbar";
  toolbar.setAttribute("role", "toolbar");
  toolbar.setAttribute("aria-label", ariaLabel);

  for (const commandId of profile.toolbarCommands) {
    const command = getCommand(commandId);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "markdown-toolbar__button";
    button.dataset.markdownCommand = command.id;
    button.setAttribute("aria-label", command.label);
    button.title = command.shortcut
      ? `${command.label} (${command.shortcut})`
      : command.label;
    button.textContent = command.label;
    button.addEventListener("click", () => execute(command.id));
    toolbar.append(button);
  }

  return toolbar;
}

