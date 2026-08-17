import type { Locale, SapModule } from "./types";
import { repositoryBranch, repositoryUrl } from "./siteConfig";

export function withBase(base: string, ...segments: string[]): string {
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  const suffix = segments
    .filter(Boolean)
    .map((segment) => segment.replace(/^\/+|\/+$/g, ""))
    .join("/");
  return suffix ? `${normalizedBase}${suffix}/` : normalizedBase;
}

export function homePath(base: string, locale: Locale): string {
  return withBase(base, locale);
}

export function askPath(base: string, locale: Locale): string {
  return withBase(base, locale, "ask");
}

export function runPath(base: string, locale: Locale): string {
  return withBase(base, locale, "run");
}

export function pluginsPath(base: string, locale: Locale): string {
  return withBase(base, locale, "plugins");
}

export function settingsPath(base: string, locale: Locale): string {
  return withBase(base, locale, "settings");
}

export function workflowsPath(base: string, locale: Locale): string {
  return withBase(base, locale, "workflows");
}

export function agentPath(base: string, locale: Locale, moduleName: SapModule, slug: string): string {
  return withBase(base, locale, "agents", moduleName, slug);
}

export function sourceUrl(moduleName: SapModule, slug: string): string {
  return `${repositoryUrl}/tree/${repositoryBranch}/agents/${moduleName}/${slug}`;
}
