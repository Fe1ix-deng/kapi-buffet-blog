import type { UIStrings } from "./types";

export { tplStr } from "./format";

const modules = import.meta.glob<{ default: UIStrings }>("./lang/*.ts", {
  eager: true,
});

const translations: Record<string, UIStrings> = {};
for (const [path, mod] of Object.entries(modules)) {
  const locale = path.slice("./lang/".length, -".ts".length);
  translations[locale] = mod.default;
}

const DEFAULT_LOCALE = "zh-CN";

/** Returns UI strings for the given locale, falling back to Chinese, then English. */
export function useTranslations(locale: string = DEFAULT_LOCALE): UIStrings {
  return (
    translations[locale] ?? translations[DEFAULT_LOCALE] ?? translations["en"]
  );
}
