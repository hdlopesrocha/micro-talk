import { ref } from "vue";

const STORAGE_KEY = "microtalk.accessToken";

function readStoredToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export const accessToken = ref(readStoredToken());
export const authRequired = ref(false);
export const tokenDialogOpen = ref(false);

export function setAccessToken(value: string): void {
  accessToken.value = value.trim();
  try {
    if (accessToken.value) localStorage.setItem(STORAGE_KEY, accessToken.value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private browsing can make storage unavailable; the in-memory token works.
  }
}

export function authHeaders(): Record<string, string> {
  return accessToken.value ? { Authorization: `Bearer ${accessToken.value}` } : {};
}

export function withToken(url: string): string {
  if (!accessToken.value) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(accessToken.value)}`;
}

export function notifyUnauthorized(): void {
  if (authRequired.value) tokenDialogOpen.value = true;
}
