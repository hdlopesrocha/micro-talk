import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import Icon from "../src/components/Icon.vue";

describe("Icon", () => {
  it("renders an accessible labelled icon", () => {
    const wrapper = mount(Icon, { props: { name: "download", label: "Download", size: 24 } });
    const svg = wrapper.get("svg");
    expect(svg.attributes("role")).toBe("img");
    expect(svg.attributes("aria-label")).toBe("Download");
    expect(svg.attributes("width")).toBe("24");
    expect(svg.get("path").attributes("d")).toContain("M12 4v12");
  });

  it("hides decorative icons and falls back to the info glyph", () => {
    const wrapper = mount(Icon, { props: { name: "does-not-exist" } });
    const svg = wrapper.get("svg");
    expect(svg.attributes("aria-hidden")).toBe("true");
    expect(svg.attributes("aria-label")).toBeUndefined();
    expect(svg.get("path").attributes("d")).toContain("M12 21a9 9 0 1 0");
  });
});
