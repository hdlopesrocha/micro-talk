import { onMounted, ref } from "vue";

const STORAGE_KEY = "microtalk.theme";

function preferredTheme(): "light" | "dark" {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // ignore storage restrictions
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const theme = ref<"light" | "dark">("dark");

  function apply(value: "light" | "dark") {
    theme.value = value;
    document.documentElement.dataset.theme = value;
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute("content", value === "dark" ? "#10141d" : "#f3f4f8");
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // ignore
    }
  }

  function toggle() {
    apply(theme.value === "dark" ? "light" : "dark");
  }

  onMounted(() => apply(preferredTheme()));

  return { theme, toggle };
}
