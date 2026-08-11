// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-15",
  devtools: { enabled: true },
  css: ["~/assets/css/main.css"],
  modules: ["@nuxtjs/i18n", "vuetify-nuxt-module", "@nuxt/fonts"],
  // Self-hosted + preloaded — avoids web-font swap layout shift.
  fonts: {
    families: [
      { name: "IBM Plex Sans", provider: "google" },
      { name: "IBM Plex Sans Arabic", provider: "google" },
      { name: "IBM Plex Mono", provider: "google" },
    ],
  },
  vuetify: {
    // Convention: no useDisplay() or viewport-branching Vuetify
    // components — SSR can't know the real viewport before hydration.
    // Use CSS display utility classes (d-none, d-md-flex) instead.
    moduleOptions: {
      // Vuetify's useLayout() collides with Nuxt's built-in useLayout()
      // (named layouts, unused here) — rename Vuetify's so neither
      // auto-import gets silently dropped.
      prefixComposables: ["useLayout"],
      // Without this, SSR always renders light while the client may
      // pick dark via prefers-color-scheme — hydration mismatch on every
      // Vuetify component. Reads the real preference via Client
      // Hints/cookie before rendering.
      ssrClientHints: {
        prefersColorScheme: true,
        prefersColorSchemeOptions: {
          useBrowserThemeOnly: false,
        },
      },
    },
    vuetifyOptions: {
      theme: {
        // Both variants required — the module validates this at startup
        // for ssrClientHints negotiation above.
        // Mashrabiya Lattice direction (see app.vue): Restrained
        // strategy, sand/walnut neutrals + one brass accent.
        defaultTheme: "light",
        themes: {
          light: {
            dark: false,
            colors: {
              background: "#F2EDE3",
              surface: "#FBF8F2",
              primary: "#B07A22",
              secondary: "#2B241C",
              "on-background": "#2B241C",
              "on-surface": "#2B241C",
            },
          },
          dark: {
            dark: true,
            colors: {
              background: "#1B1610",
              surface: "#241E16",
              primary: "#D9A648",
              secondary: "#EFE6D6",
              "on-background": "#F2EDE3",
              "on-surface": "#F2EDE3",
            },
          },
        },
      },
      // No manual locale/rtl config — vuetify-nuxt-module auto-bridges
      // to @nuxtjs/i18n (RTL from each locale's dir below) once
      // i18n.vueI18n is set.
    },
  },
  // package.json overrides @intlify/vue-i18n-extensions to ^9.0.0 — 8.0.0
  // (pulled transitively via @nuxtjs/i18n) hard-pins deprecated
  // vue-i18n@10; 9.0.0 uses the v11 already in the tree.
  i18n: {
    locales: [
      { code: "en", language: "en-US", name: "English", dir: "ltr" },
      { code: "ar", language: "ar-SA", name: "العربية", dir: "rtl" },
    ],
    defaultLocale: "en",
    strategy: "no_prefix",
    // Full message set (app strings + Vuetify's $vuetify strings) lives
    // in i18n.config.ts.
    vueI18n: "./i18n.config.ts",
    // Persists language choice in a cookie. redirectOn is a no-op under
    // no_prefix, but the cookie still sets the initial locale on return.
    detectBrowserLanguage: {
      useCookie: true,
      cookieKey: "sarjy_locale",
      redirectOn: "root",
    },
  },
  runtimeConfig: {
    livekitApiKey: "",
    livekitApiSecret: "",
    public: {
      livekitUrl: "",
    },
  },
});
