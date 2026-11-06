<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { accessToken } from "../api/auth";
import Icon from "./Icon.vue";

const props = defineProps<{
  open: boolean;
  authRequired: boolean;
  busy?: boolean;
  error?: string;
}>();

const emit = defineEmits<{
  save: [token: string];
  close: [];
}>();

const value = ref("");
const input = ref<HTMLInputElement | null>(null);

watch(
  () => props.open,
  (open) => {
    if (open) {
      value.value = accessToken.value;
      nextTick(() => input.value?.focus());
    }
  },
);

function submit() {
  emit("save", value.value.trim());
}
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog">
      <div v-if="open" class="overlay" role="presentation" @click.self="emit('close')">
        <div
          class="dialog card"
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-title"
          @keydown.esc="!authRequired && emit('close')"
        >
          <div class="dialog-head">
            <div class="dialog-mark"><Icon name="settings" :size="22" /></div>
            <button
              v-if="!authRequired"
              class="icon-btn small"
              type="button"
              aria-label="Close"
              @click="emit('close')"
            >
              <Icon name="x" :size="16" />
            </button>
          </div>
          <h2 id="token-title">
            {{ authRequired ? "Access token required" : "Connection settings" }}
          </h2>
          <p class="soft">
            {{
              authRequired
                ? "This MicroTalk instance is protected. Enter the token configured in GATEWAY_AUTH_TOKEN."
                : "Optionally store the access token configured for this MicroTalk instance."
            }}
          </p>
          <form class="stack" @submit.prevent="submit">
            <div class="field">
              <label for="token-input">Access token</label>
              <input
                id="token-input"
                ref="input"
                v-model="value"
                class="input"
                type="password"
                autocomplete="current-password"
                placeholder="Paste the gateway token"
              />
              <span class="field-hint">
                Stored only in this browser's local storage. It is sent to the gateway with API
                requests; audio and event streams include it as a query parameter because browser
                media elements cannot set headers.
              </span>
            </div>
            <div v-if="error" class="alert error" role="alert">
              <Icon name="alert" :size="18" />
              <span>{{ error }}</span>
            </div>
            <div class="row between">
              <span class="tiny muted">
                <Icon name="layers" :size="13" /> Token never leaves your machine
              </span>
              <div class="row">
                <button
                  v-if="!authRequired"
                  class="btn ghost small"
                  type="button"
                  @click="emit('save', '')"
                >
                  Clear token
                </button>
                <button class="btn primary" type="submit" :disabled="busy || (authRequired && !value.trim())">
                  <span v-if="busy" class="spinner"></span>
                  <Icon v-else name="check" :size="16" />
                  Save &amp; connect
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(4, 7, 12, 0.66);
  backdrop-filter: blur(6px);
}

.dialog {
  width: min(480px, 100%);
  padding: 28px;
  box-shadow: var(--shadow);
  animation: rise 0.22s ease both;
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 15px;
}

.dialog-mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  background: var(--accent-soft);
  color: var(--accent);
}

h2 {
  font-size: 22px;
  margin-bottom: 8px;
}

p {
  margin-bottom: 20px;
  font-size: 14px;
}

.dialog-enter-active,
.dialog-leave-active {
  transition: opacity 0.18s ease;
}

.dialog-enter-from,
.dialog-leave-to {
  opacity: 0;
}
</style>
