export const DIRECT_TRANSLATION_PAIRS = ["pt-en", "en-pt", "en-fr", "fr-en"] as const;

function baseLanguage(value: string | null | undefined): string | null {
  return (value || "").split("-")[0] || null;
}

/**
 * Return the ordered OPUS-MT pairs needed for a translation direction.
 * Portuguese↔French is routed through English and therefore needs two models.
 */
export function translationRoute(
  source: string | null | undefined,
  target: string | null | undefined,
): string[] {
  const from = baseLanguage(source);
  const to = baseLanguage(target);
  if (!from || !to || from === to) return [];
  const direct = `${from}-${to}`;
  if ((DIRECT_TRANSLATION_PAIRS as readonly string[]).includes(direct)) return [direct];
  if (["pt", "fr"].includes(from) && ["pt", "fr"].includes(to)) {
    return [`${from}-en`, `en-${to}`];
  }
  return [];
}

export function missingTranslationPairs(
  route: string[],
  supportedPairs: Record<string, boolean>,
): string[] {
  return route.filter((pair) => supportedPairs[pair] === false);
}

export function translationRouteLabel(route: string[]): string {
  return route.map((pair) => pair.toUpperCase().replace("-", " → ")).join(" + ");
}
