import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  output: "static",
  trailingSlash: "always",
  build: {
    format: "directory",
  },
  // sitemap.xml + robots.txt-friendly index — 'site' wordt op deploy-tijd
  // gevuld via env var of overschreven, zodat batch-build geen domain
  // hardcoded heeft.
  site: process.env.PUBLIC_SITE_URL || "https://example.com",
  integrations: [sitemap()],
});
