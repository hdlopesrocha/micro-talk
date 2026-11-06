<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, handleApiError } from "./api/client";
import { accessToken, authRequired, setAccessToken, tokenDialogOpen } from "./api/auth";
import type { Catalog, JobState } from "./api/types";
import { useTheme } from "./composables/useTheme";
import AppHeader from "./components/AppHeader.vue";
import CreateWorkflow from "./components/CreateWorkflow.vue";
import DirectTools from "./components/DirectTools.vue";
import Icon from "./components/Icon.vue";
import JobLibrary from "./components/JobLibrary.vue";
import JobView from "./components/JobView.vue";
import TokenDialog from "./components/TokenDialog.vue";

type View = "create" | "library" | "tools" | "job";

const { theme, toggle } = useTheme();
const view = ref<View>("create");
const activeJobId = ref("");
const catalog = ref<Catalog | null>(null);
const catalogLoading = ref(true);
const catalogError = ref("");
const booting = ref(true);
const bootError = ref("");
const tokenBusy = ref(false);
const tokenError = ref("");

const needsToken = computed(() => authRequired.value && !accessToken.value);

watch(view, (current) => {
  if (current === "create") document.title = "MicroTalk · Local document studio";
  if (current === "library") document.title = "Library · MicroTalk";
  if (current === "tools") document.title = "Quick tools · MicroTalk";
});

onMounted(() => {
  syncRoute();
  window.addEventListener("hashchange", syncRoute);
  void boot();
});

onBeforeUnmount(() => {
  window.removeEventListener("hashchange", syncRoute);
});

function parseRoute(): { view: View; jobId: string } {
  const raw = window.location.hash.replace(/^#\/?/, "").replace(/\/+$/, "");
  const parts = raw.split("/").filter(Boolean).map((part) => decodeURIComponent(part));
  if (parts[0] === "jobs" && parts[1]) return { view: "job", jobId: parts[1] };
  if (parts[0] === "library") return { view: "library", jobId: "" };
  if (parts[0] === "tools") return { view: "tools", jobId: "" };
  return { view: "create", jobId: "" };
}

function syncRoute() {
  const route = parseRoute();
  view.value = route.view;
  if (route.jobId) activeJobId.value = route.jobId;
}

function setRoute(next: View, jobId = "") {
  view.value = next;
  activeJobId.value = jobId;
  const hash =
    next === "job" && jobId
      ? `#/jobs/${encodeURIComponent(jobId)}`
      : next === "library"
        ? "#/library"
        : next === "tools"
          ? "#/tools"
          : "#/create";
  if (window.location.hash !== hash) window.location.hash = hash;
}

function openJob(job: JobState | string) {
  setRoute("job", typeof job === "string" ? job : job.id);
}

function openLibrary() {
  setRoute("library");
}

function newWorkflow() {
  setRoute("create");
}

function openSettings() {
  tokenError.value = "";
  tokenDialogOpen.value = true;
}

function closeTokenDialog() {
  if (!authRequired.value) tokenDialogOpen.value = false;
}

async function boot() {
  booting.value = true;
  bootError.value = "";
  try {
    const status = await api.authStatus();
    authRequired.value = Boolean(status.auth_required);
    if (status.auth_required && !accessToken.value) {
      tokenDialogOpen.value = true;
      catalogLoading.value = false;
      return;
    }
    await loadCatalog();
  } catch (error) {
    bootError.value = handleApiError(error);
  } finally {
    booting.value = false;
  }
}

async function loadCatalog(silent = false) {
  if (!silent) catalogLoading.value = true;
  try {
    catalog.value = await api.catalog();
    catalogError.value = "";
    if (typeof catalog.value.auth_required === "boolean") {
      authRequired.value = catalog.value.auth_required;
    }
  } catch (error) {
    catalogError.value = handleApiError(error);
  } finally {
    catalogLoading.value = false;
  }
}

async function saveToken(value: string) {
  tokenError.value = "";
  if (authRequired.value && !value) {
    tokenError.value = "Enter the access token configured for this instance.";
    return;
  }
  tokenBusy.value = true;
  setAccessToken(value);
  try {
    await api.checkToken();
    tokenDialogOpen.value = false;
    await loadCatalog();
  } catch (error) {
    tokenError.value = handleApiError(error);
  } finally {
    tokenBusy.value = false;
  }
}

function retryConnection() {
  bootError.value = "";
  void boot();
}
</script>

<template>
  <AppHeader
    :view="view"
    :theme="theme"
    :auth-required="authRequired"
    @navigate="setRoute"
    @toggle-theme="toggle"
    @settings="openSettings"
  />

  <main class="page">
    <div class="container">
      <div v-if="booting" class="card panel boot-panel">
        <span class="boot-mark"><span class="spinner large"></span></span>
        <div>
          <h1>Starting MicroTalk</h1>
          <p class="muted">Checking the local gateway and model catalog…</p>
        </div>
      </div>

      <div v-else-if="bootError" class="card panel boot-panel boot-error">
        <span class="boot-mark danger"><Icon name="plug" :size="24" /></span>
        <div class="grow">
          <h1>Gateway unreachable</h1>
          <p class="soft">{{ bootError }}</p>
          <p class="tiny muted">
            Start the stack with <code>docker compose up -d</code> and check that Nginx is listening
            on the configured port.
          </p>
          <div class="row wrap boot-actions">
            <button class="btn primary" type="button" @click="retryConnection">
              <Icon name="refresh" :size="16" /> Retry
            </button>
            <button class="btn ghost" type="button" @click="openSettings">
              <Icon name="settings" :size="16" /> Connection settings
            </button>
          </div>
        </div>
      </div>

      <div v-else-if="needsToken" class="card panel boot-panel">
        <span class="boot-mark"><Icon name="bookmark" :size="24" /></span>
        <div class="grow">
          <h1>Access token required</h1>
          <p class="soft">
            This MicroTalk instance is protected. Open connection settings and enter the token from
            <code>GATEWAY_AUTH_TOKEN</code>.
          </p>
          <button class="btn primary" type="button" @click="openSettings">
            <Icon name="settings" :size="16" /> Enter token
          </button>
        </div>
      </div>

      <template v-else>
        <div v-if="catalogError" class="alert error catalog-error" role="alert">
          <Icon name="alert" :size="18" />
          <span class="grow">
            {{ catalogError }}
            <template v-if="authRequired"> Add a valid token in connection settings.</template>
          </span>
          <button class="btn ghost small" type="button" @click="loadCatalog()">Retry</button>
          <button class="btn ghost small" type="button" @click="openSettings">
            <Icon name="settings" :size="14" /> Settings
          </button>
        </div>

        <CreateWorkflow
          v-if="view === 'create'"
          :catalog="catalog"
          :catalog-loading="catalogLoading"
          @created="openJob"
        />
        <JobLibrary
          v-else-if="view === 'library'"
          @open="openJob"
          @create="newWorkflow"
        />
        <DirectTools
          v-else-if="view === 'tools'"
          :catalog="catalog"
          :catalog-loading="catalogLoading"
        />
        <JobView
          v-else
          :job-id="activeJobId"
          @open-library="openLibrary"
          @new-workflow="newWorkflow"
          @deleted="openLibrary"
        />
      </template>
    </div>
  </main>

  <footer class="app-footer">
    <div class="container footer-inner">
      <div class="row wrap">
        <span class="footer-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></span>
        <span><strong>MicroTalk</strong> · local document studio</span>
      </div>
      <div class="row wrap footer-links">
        <span><Icon name="layers" :size="13" /> PDF · Translate · Summarize · Piper · FFmpeg</span>
        <span><Icon name="plug" :size="13" /> Only Nginx is exposed</span>
      </div>
    </div>
  </footer>

  <TokenDialog
    :open="tokenDialogOpen"
    :auth-required="authRequired"
    :busy="tokenBusy"
    :error="tokenError"
    @save="saveToken"
    @close="closeTokenDialog"
  />
</template>

<style scoped>
.boot-panel {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-top: 40px;
  min-height: 170px;
}

.boot-mark {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: var(--accent-soft);
  color: var(--accent);
  flex: 0 0 auto;
}

.boot-mark.danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.boot-panel h1 {
  font-size: 23px;
  margin-bottom: 5px;
}

.boot-panel p {
  margin: 0 0 6px;
  max-width: 720px;
  font-size: 14px;
}

.boot-actions {
  margin-top: 13px;
  gap: 8px;
}

.catalog-error {
  margin-bottom: 17px;
  align-items: center;
}

.app-footer {
  border-top: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 55%, transparent);
  padding: 20px 0 28px;
}

.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 12px;
}

.footer-inner strong {
  color: var(--text-soft);
}

.footer-links {
  gap: 15px;
}

.footer-links span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.footer-mark {
  display: grid;
  grid-template-columns: repeat(2, 5px);
  grid-template-rows: repeat(2, 5px);
  gap: 2px;
  width: 17px;
  height: 17px;
  padding: 2px;
  border-radius: 5px;
  background: linear-gradient(145deg, var(--accent), #7f5df5);
}

.footer-mark span {
  border-radius: 1px;
  background: rgba(255, 255, 255, 0.9);
}

.footer-mark span:nth-child(2) {
  opacity: 0.68;
}
.footer-mark span:nth-child(3) {
  opacity: 0.5;
}
.footer-mark span:nth-child(4) {
  opacity: 0.84;
}

code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--surface-3);
  color: var(--text);
  font-size: 12px;
  font-family: var(--font-mono);
}

@media (max-width: 620px) {
  .boot-panel {
    margin-top: 16px;
    align-items: flex-start;
    gap: 13px;
  }
  .boot-mark {
    width: 48px;
    height: 48px;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
