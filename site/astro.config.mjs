import { defineConfig } from "astro/config";
import react from "@astrojs/react";

const isDevelopment = process.env.NODE_ENV === "development";
const siteOrigin = process.env.PUBLIC_SITE_ORIGIN ?? "https://yin-wen-bin.github.io";
const productionBase = process.env.PUBLIC_SITE_BASE ?? "/SAPBusinessAgents";

export default defineConfig({
  integrations: [react()],
  site: siteOrigin,
  base: isDevelopment ? "/" : productionBase,
  output: "static",
  trailingSlash: "always",
});
