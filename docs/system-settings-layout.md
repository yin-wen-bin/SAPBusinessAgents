# System settings navigation / 系统配置导航

Updated: 2026-10-20

## Scope

This is a frontend presentation change, not a Runtime admission change. The settings page keeps its URL and uses `section=runtime|history` and `provider=<id>` for view state. Viewing a Provider is not selecting the system default.

系统配置分为 AI Runtime 和查询历史。默认摘要来自接口实际保存值，不使用设计图示例。页面不再同时展开所有 SDK。

- Implemented, platform-supported SDKs appear in API order regardless of enablement. Unknown status keeps its prior grouping. The default remains discoverable when blocked.
- Up to four direct tabs; beyond four, three tabs plus More. At 820px or narrower, use the labelled Runtime selector. Model IDs never become Provider tabs.
- Account and model sections use two columns only when the content area is at least 760px. Maintenance and authentication details start collapsed; errors and pending authentication remain visible.
- Tab/section/model selection is local viewing state. Status reread uses the existing GET. Installation/authentication checks, model checking/saving, enable/disable and default switching remain explicit actions with the existing backend rules.
- Model catalogs load on first viewing of a supported Provider. Hidden panels retain inputs and disclosure state. Polling updates status without rebuilding unchanged forms. A changed binding blocks stale forms and asks the user to discard/recheck rather than silently applying them.
- Query history retains its existing form and API. No public API, database, Runtime configuration or qualification algorithm was added.

## Implementation boundaries

`settingsNavigation.ts` owns view navigation only. `SystemSettingsPanel.astro` continues to own existing request and operation-recovery handlers. `settings-layout.css` is settings-scoped; other pages retain their business-module sidebar.

SDK configuration, authentication, model compatibility, SDK integration and business evaluation remain separate facts. Quality records do not grant execution rights. The frontend must not infer `enabled=false` or unauthenticated from unknown status.

Advanced maintenance uses compact versions, diagnostics and risk-action groups.
See [Runtime maintenance](runtime-maintenance.md) for version-only reads, pinned
WorkBuddy repair limitations and unknown update outcomes.

## Verification and deployment

Use the existing frontend tests plus `settings-navigation.browser.mjs`, `runtime-status.browser.mjs`, `runtime-authentication.browser.mjs`, `workbuddy-service-site.browser.mjs` and `workbuddy-qualification.browser.mjs`. Browser suites serve the real built UI with synthetic APIs; they must not reach SDKs or SAP. Set `SAPBA_BROWSER_DIST`, `SAPBA_BROWSER_REPORT` and an installed `SAPBA_PLAYWRIGHT_MODULE` as needed.

The qualification fixture supplies `business_evaluation.required_operation_count`, as the existing UI already reads the API's dynamic denominator. This does not change production qualification rules.

Deploy only through the existing `Start-SAPBusinessAgents.ps1 -RestartSiteOnly -PreserveSiteBuilds -NoBrowser` path after an isolated build and predeployment checks. Retain the previous build. Verify API process identity and protected-state digests before and after; inspect the live page through GET-only actions. Do not run authentication/model checks as a deployment smoke test.
