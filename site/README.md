# SAP Business Agents site

Astro static catalog for the Agent manifests under `../agents/`. The production build uses the GitHub Pages base path `/SAPBusinessAgents/` and generates localized catalog and detail pages.

```powershell
npm ci
npm run validate
npm run check
npm test
```

Use `npm run dev` for local development. The development server intentionally uses `/` as its base path while production builds use `/SAPBusinessAgents/`.
