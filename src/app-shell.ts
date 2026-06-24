export type AppTab = "config" | "task" | "validation";

const tabs: AppTab[] = ["config", "task", "validation"];

export function resolveAppTab(value: string | null): AppTab {
  return tabs.includes(value as AppTab) ? (value as AppTab) : "config";
}

export function adjacentAppTab(tab: AppTab, step: 1 | -1): AppTab {
  const current = tabs.indexOf(tab);
  return tabs[(current + step + tabs.length) % tabs.length];
}

export function createAppTabs(root: ParentNode, storage: Storage, storageKey: string) {
  const activate = (tab: AppTab, focus = false): void => {
    root.querySelectorAll<HTMLButtonElement>("button[data-app-tab]").forEach((button) => {
      const active = button.dataset.appTab === tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    root.querySelectorAll<HTMLElement>("[data-tab-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== tab;
    });
    storage.setItem(storageKey, tab);
  };

  const bind = (): void => {
    const buttons = [...root.querySelectorAll<HTMLButtonElement>("button[data-app-tab]")];
    const saved = storage.getItem(storageKey);
    const initial = resolveAppTab(saved);
    for (const button of buttons) {
      button.addEventListener("click", () => activate(button.dataset.appTab as AppTab));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
        const targetTab = adjacentAppTab(button.dataset.appTab as AppTab, step);
        activate(targetTab, true);
      });
    }
    activate(initial);
  };

  return { activate, bind };
}
