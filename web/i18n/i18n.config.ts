import ar from "./locales/ar";
import en from "./locales/en";

// Referenced by nuxt.config.ts's i18n.vueI18n — lets vuetify-nuxt-module
// auto-bridge locale/RTL to @nuxtjs/i18n. No hand-written sync plugin.
export default defineI18nConfig(() => ({
  legacy: false,
  locale: "en",
  messages: { en, ar },
}));
