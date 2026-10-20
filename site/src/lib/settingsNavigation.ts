/** View-only navigation. Never owns or infers Runtime execution permission. */
type Runtime = { provider_id: string; name?: Record<string, string>; provider_implemented?: boolean; platform_supported?: boolean; enabled?: boolean | null; default_model_id?: string; blockers?: string[]; authentication?: { operation?: { status?: string }; failure_code?: string }; status_refresh?: { stale?: boolean } };
export function runtimeGroups(items: Runtime[], currentDefault: string | null, previous = new Map<string, boolean>()) {
  return items.map(item => {
    const knownOther = item.provider_implemented === false || item.platform_supported === false;
    const knownMain = item.provider_implemented === true && item.platform_supported === true;
    const main = item.provider_id === currentDefault || (knownOther ? false : knownMain ? true : previous.get(item.provider_id) ?? true);
    previous.set(item.provider_id, main);
    return { item, main };
  });
}
export function initializeSettingsNavigation() {
  const found = document.querySelector<HTMLElement>("[data-settings-shell]");
  if (!found) return;
  const shell: HTMLElement = found;
  const zh = shell.dataset.locale === "zh";
  const text = (cn: string, en: string) => zh ? cn : en;
  const tabs = shell.querySelector<HTMLElement>("[data-runtime-tabs]")!;
  const picker = shell.querySelector<HTMLSelectElement>("[data-runtime-picker]")!;
  const more = shell.querySelector<HTMLSelectElement>("[data-runtime-more]")!;
  const others = shell.querySelector<HTMLElement>("[data-other-runtimes]")!;
  const defaultLabel = shell.querySelector<HTMLElement>("[data-default-summary]")!;
  const previous = new Map<string, boolean>();
  let items: Runtime[] = [], currentDefault: string | null = null;
  let active = new URL(location.href).searchParams.get("provider") || "";
  let section = new URL(location.href).searchParams.get("section") === "history" ? "history" : "runtime";
  let signature = "", received = false;
  const name = (item: Runtime) => item.name?.[shell.dataset.locale!] || item.name?.en || item.provider_id;
  const label = (item: Runtime) => {
    const operation = item.authentication?.operation?.status;
    const activity = shell.querySelector<HTMLElement>(`[data-provider-panel="${CSS.escape(item.provider_id)}"]`)?.dataset.activity;
    const status = activity || (operation && ["queued", "running", "waiting_authentication", "cancelling", "cleanup_pending"].includes(operation) ? text("操作进行中", "Operation in progress") : item.authentication?.failure_code ? text("需处理", "Needs attention") : item.enabled === true ? text("已启用", "Enabled") : item.enabled === false ? text("已停用", "Disabled") : text("待核对", "Not confirmed"));
    return `${name(item)} · ${status}${item.provider_id === currentDefault ? text(" · 系统默认", " · System default") : ""}`;
  };
  function updateURL(push = false) {
    const url = new URL(location.href); url.searchParams.set("section", section);
    if (active) url.searchParams.set("provider", active); else url.searchParams.delete("provider");
    if (url.href !== location.href) history[push ? "pushState" : "replaceState"]({}, "", url);
    document.querySelectorAll<HTMLAnchorElement>("[data-language-link]").forEach(link => {
      const alternate = new URL(link.href); alternate.search = url.search; link.href = alternate.href;
    });
    shell.querySelectorAll<HTMLAnchorElement>("[data-settings-section]").forEach(link => {
      const target = new URL(url); target.searchParams.set("section", link.dataset.settingsSection!); link.href = target.href;
    });
  }
  function choose(provider: string, push = true) {
    active = provider; section = "runtime"; updateURL(push); display();
  }
  function display() {
    if (items.length && !items.some(item => item.provider_id === active)) {
      active = items.find(item => item.provider_id === currentDefault)?.provider_id || runtimeGroups(items, currentDefault, previous).find(entry => entry.main)?.item.provider_id || items[0].provider_id;
    }
    updateURL();
    shell.querySelectorAll<HTMLElement>("[data-settings-pane]").forEach(pane => { pane.hidden = pane.dataset.settingsPane !== section; });
    shell.querySelectorAll<HTMLElement>("[data-settings-section]").forEach(link => { if (link.dataset.settingsSection === section) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
    shell.querySelectorAll<HTMLElement>("[data-provider-panel]").forEach(panel => { panel.hidden = panel.dataset.providerPanel !== active; });
    const selected = items.find(item => item.provider_id === currentDefault);
    defaultLabel.textContent = selected ? `${text("系统默认", "System default")}: ${name(selected)} · ${selected.default_model_id || text("模型待核对", "Model not confirmed")}` : !received ? text("正在读取系统默认 Runtime…", "Reading the system-default Runtime…") : currentDefault ? `${text("系统默认", "System default")}: ${currentDefault} · ${text("状态待核对", "State not confirmed")}` : text("尚未设置系统默认 Runtime", "No system-default Runtime");
    const grouped = runtimeGroups(items, currentDefault, previous);
    const main = grouped.filter(entry => entry.main).map(entry => entry.item);
    const secondary = grouped.filter(entry => !entry.main).map(entry => entry.item);
    const nextSignature = JSON.stringify([main.map(item => [item.provider_id, label(item)]), secondary.map(item => [item.provider_id, label(item)]), active]);
    if (nextSignature !== signature) {
      const focusId = (document.activeElement as HTMLElement)?.dataset.runtimeTab;
      signature = nextSignature;
      tabs.replaceChildren(); picker.replaceChildren(); more.replaceChildren(); others.replaceChildren();
      const direct = main.length > 4 ? main.slice(0, 3) : main;
      for (const item of direct) {
        const button = document.createElement("button"); button.type = "button"; button.textContent = label(item);
        button.dataset.runtimeTab = item.provider_id; button.id = `runtime-tab-${item.provider_id}`;
        button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(item.provider_id === active)); button.setAttribute("aria-controls", `runtime-panel-${item.provider_id}`);
        button.tabIndex = item.provider_id === active || !direct.some(entry => entry.provider_id === active) && item === direct[0] ? 0 : -1;
        button.addEventListener("click", () => choose(item.provider_id)); tabs.append(button);
      }
      function option(item: Runtime, select: HTMLSelectElement) { const opt = document.createElement("option"); opt.value = item.provider_id; opt.textContent = label(item); select.append(opt); }
      for (const item of [...main, ...secondary]) option(item, picker);
      const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = text("更多 Runtime", "More runtimes"); more.append(placeholder);
      for (const item of main.slice(direct.length)) option(item, more);
      more.parentElement!.hidden = main.length <= 4;
      for (const item of secondary) {
        const button = document.createElement("button"); button.type = "button"; button.textContent = `${label(item)} — ${item.provider_implemented === false ? text("尚未接入", "Not integrated") : text("当前平台不支持", "Platform unsupported")}`;
        button.addEventListener("click", () => choose(item.provider_id)); others.append(button);
      }
      (others.closest("details") as HTMLDetailsElement).hidden = !secondary.length;
      if (focusId) tabs.querySelector<HTMLButtonElement>(`[data-runtime-tab="${CSS.escape(focusId)}"]`)?.focus();
    }
    picker.value = active; more.value = main.slice(main.length > 4 ? 3 : main.length).some(item => item.provider_id === active) ? active : "";
    document.dispatchEvent(new CustomEvent("sapba:settings-select", {detail: {provider: active, section}}));
  }
  tabs.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const buttons = [...tabs.querySelectorAll<HTMLButtonElement>("[role=tab]")]; if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
    event.preventDefault(); choose(buttons[next].dataset.runtimeTab!); tabs.querySelector<HTMLButtonElement>(`[data-runtime-tab="${CSS.escape(active)}"]`)?.focus();
  });
  picker.addEventListener("change", () => choose(picker.value)); more.addEventListener("change", () => { if (more.value) choose(more.value); });
  shell.querySelectorAll<HTMLAnchorElement>("[data-settings-section]").forEach(link => link.addEventListener("click", event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); section = link.dataset.settingsSection!; updateURL(true); display();
  }));
  window.addEventListener("popstate", () => { const params = new URL(location.href).searchParams; active = params.get("provider") || ""; section = params.get("section") === "history" ? "history" : "runtime"; display(); });
  document.addEventListener("sapba:settings-state", ((event: CustomEvent) => { received = true; items = event.detail.items; currentDefault = event.detail.defaultProviderId; display(); }) as EventListener);
  document.addEventListener("sapba:settings-activity", () => display());
  display(); document.dispatchEvent(new CustomEvent("sapba:settings-request-state"));
}
