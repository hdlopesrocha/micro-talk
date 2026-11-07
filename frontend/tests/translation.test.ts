import { describe, expect, it } from "vitest";
import {
  missingTranslationPairs,
  translationRoute,
  translationRouteLabel,
} from "../src/utils/translation";

describe("translation routes", () => {
  it("uses direct models for Portuguese/English and English/French", () => {
    expect(translationRoute("pt", "en")).toEqual(["pt-en"]);
    expect(translationRoute("en", "pt-BR")).toEqual(["en-pt"]);
    expect(translationRoute("en", "fr")).toEqual(["en-fr"]);
    expect(translationRoute("fr", "en")).toEqual(["fr-en"]);
  });

  it("pivots Portuguese/French through English in both directions", () => {
    expect(translationRoute("pt", "fr")).toEqual(["pt-en", "en-fr"]);
    expect(translationRoute("fr", "pt-PT")).toEqual(["fr-en", "en-pt"]);
  });

  it("reports missing models and labels multi-model routes", () => {
    expect(missingTranslationPairs(["pt-en", "en-fr"], { "pt-en": true, "en-fr": false })).toEqual([
      "en-fr",
    ]);
    expect(translationRouteLabel(["fr-en", "en-pt"])).toBe("FR → EN + EN → PT");
  });
});
