<script setup lang="ts">
import Icon from "./Icon.vue";

defineProps<{
  view: "create" | "library" | "tools" | "job";
  theme: "light" | "dark";
  authRequired: boolean;
}>();

defineEmits<{
  navigate: [view: "create" | "library" | "tools"];
  toggleTheme: [];
  settings: [];
}>();
</script>

<template>
  <header class="topbar">
    <div class="container header-inner">
      <button class="brand" type="button" @click="$emit('navigate', 'create')">
        <span class="brand-mark" aria-hidden="true">
          <span></span><span></span><span></span><span></span>
        </span>
        <span class="brand-text">
          <strong>MicroTalk</strong>
          <small>Local document studio</small>
        </span>
      </button>

      <nav class="nav" aria-label="Main navigation">
        <button
          type="button"
          :class="{ active: view === 'create' || view === 'job' }"
          @click="$emit('navigate', 'create')"
        >
          <Icon name="plus" :size="17" />
          <span>Create</span>
        </button>
        <button
          type="button"
          :class="{ active: view === 'library' }"
          @click="$emit('navigate', 'library')"
        >
          <Icon name="library" :size="17" />
          <span>Library</span>
        </button>
        <button
          type="button"
          :class="{ active: view === 'tools' }"
          @click="$emit('navigate', 'tools')"
        >
          <Icon name="tools" :size="17" />
          <span>Quick tools</span>
        </button>
      </nav>

      <div class="actions">
        <span class="local-pill" title="All processing happens on this machine">
          <Icon name="layers" :size="14" />
          <span>100% local</span>
        </span>
        <button
          class="icon-btn"
          type="button"
          :aria-label="theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
          :title="theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
          @click="$emit('toggleTheme')"
        >
          <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="18" />
        </button>
        <button
          class="icon-btn settings"
          type="button"
          aria-label="Connection settings"
          title="Connection settings"
          @click="$emit('settings')"
        >
          <Icon name="settings" :size="18" />
          <span v-if="authRequired" class="auth-dot" aria-hidden="true"></span>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 40;
  height: var(--header-height);
  border-bottom: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
  background: color-mix(in srgb, var(--bg) 78%, transparent);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}

.header-inner {
  height: 100%;
  display: flex;
  align-items: center;
  gap: 24px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  border: 0;
  background: transparent;
  padding: 4px;
  border-radius: 10px;
  color: var(--text);
  text-align: left;
}

.brand-mark {
  display: grid;
  grid-template-columns: repeat(2, 9px);
  grid-template-rows: repeat(2, 9px);
  gap: 3px;
  width: 31px;
  height: 31px;
  padding: 4px;
  border-radius: 9px;
  background: linear-gradient(145deg, var(--accent), #7f5df5);
  box-shadow: 0 6px 18px rgba(91, 124, 242, 0.3);
}

.brand-mark span {
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.9);
}

.brand-mark span:nth-child(2) {
  opacity: 0.68;
}
.brand-mark span:nth-child(3) {
  opacity: 0.5;
}
.brand-mark span:nth-child(4) {
  opacity: 0.84;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-text strong {
  font-size: 16px;
  letter-spacing: -0.02em;
}

.brand-text small {
  color: var(--muted);
  font-size: 10.5px;
  letter-spacing: 0.02em;
}

.nav {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.nav button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 0;
  background: transparent;
  color: var(--muted);
  border-radius: 8px;
  padding: 7px 12px;
  font-size: 13.5px;
  font-weight: 600;
  transition: 0.15s ease;
}

.nav button:hover {
  color: var(--text);
}

.nav button.active {
  color: var(--text);
  background: var(--surface-3);
}

.actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 9px;
}

.local-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid color-mix(in srgb, var(--teal) 30%, transparent);
  border-radius: 100px;
  background: var(--teal-soft);
  color: var(--teal);
  font-size: 11.5px;
  font-weight: 650;
  letter-spacing: 0.01em;
}

.settings {
  position: relative;
}

.auth-dot {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--amber);
  border: 1px solid var(--surface);
}

@media (max-width: 860px) {
  .header-inner {
    gap: 12px;
  }
  .brand-text small,
  .local-pill,
  .nav button span {
    display: none;
  }
  .nav button {
    padding: 8px;
  }
  .actions .icon-btn {
    width: 36px;
    height: 36px;
  }
}

@media (max-width: 520px) {
  .brand-text {
    display: none;
  }
  .header-inner {
    gap: 8px;
  }
  .nav {
    margin-left: auto;
  }
}
</style>
