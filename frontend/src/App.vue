<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useRunsStore } from './stores/runs.js'
import { STATUS_LABELS, shortId } from './utils/format.js'

const route = useRoute()
const store = useRunsStore()

onMounted(() => {
  store.fetchRuns()
  store.fetchConfig()
})

const navItems = [
  {
    name: 'dashboard',
    to: '/',
    label: '研究工作台',
    icon: 'M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5',
  },
  {
    name: 'new-research',
    to: '/new',
    label: '新建研究',
    icon: 'M12 5v14M5 12h14',
  },
  {
    name: 'archive',
    to: '/runs',
    label: '历史卷宗',
    icon: 'M4 5h16v4H4zM6 9v10h12V9M10 13h4',
  },
]

const activeRuns = computed(() => store.activeRuns.slice(0, 4))

function isActive(item) {
  if (item.name === 'dashboard') return route.name === 'dashboard'
  if (item.name === 'archive') {
    return ['archive', 'run-detail', 'report'].includes(route.name)
  }
  return route.name === item.name
}
</script>

<template>
  <div class="shell">
    <aside class="rail">
      <RouterLink to="/" class="brand">
        <span class="brand-seal">OC</span>
        <span class="brand-text">
          <span class="brand-name">Open<em>Compete</em></span>
          <span class="brand-sub">竞品研究工作台</span>
        </span>
      </RouterLink>

      <nav class="nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item--active': isActive(item) }"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path :d="item.icon" />
          </svg>
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div v-if="activeRuns.length" class="rail-section">
        <div class="rail-heading">进行中</div>
        <RouterLink
          v-for="run in activeRuns"
          :key="run.run_id"
          :to="`/runs/${run.run_id}`"
          class="rail-run"
        >
          <span class="rail-run-dot" :class="`dot--${run.status}`"></span>
          <span class="rail-run-body">
            <span class="rail-run-query">{{ run.query }}</span>
            <span class="rail-run-meta mono">{{ shortId(run.run_id) }} · {{ STATUS_LABELS[run.status] }}</span>
          </span>
        </RouterLink>
      </div>

      <div class="rail-foot">
        <div class="rail-foot-row mono">.opencompete-data/</div>
        <div class="rail-foot-row">本地运行 · 无远程依赖</div>
        <div class="rail-foot-row mono">v0.1 · upstream-b764481f</div>
      </div>
    </aside>

    <main class="main">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" :key="route.fullPath" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

/* ============ 档案柜侧栏 ============ */
.rail {
  width: 236px;
  flex-shrink: 0;
  background: var(--ink);
  color: var(--paper);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 3px solid var(--vermilion);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 26px 22px 24px;
  text-decoration: none;
  color: inherit;
  border-bottom: 1px solid rgba(244, 238, 225, 0.14);
}

.brand-seal {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: var(--vermilion);
  color: #fdf6ee;
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 16px;
  border-radius: 4px;
  transform: rotate(-4deg);
  box-shadow: 2px 2px 0 rgba(0, 0, 0, 0.35);
}

.brand-name {
  display: block;
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.01em;
  line-height: 1.15;
}

.brand-name em {
  font-style: italic;
  color: #e8b48e;
}

.brand-sub {
  display: block;
  font-size: 11px;
  letter-spacing: 0.28em;
  color: rgba(244, 238, 225, 0.55);
  margin-top: 3px;
}

.nav {
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 4px;
  text-decoration: none;
  color: rgba(244, 238, 225, 0.72);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.05em;
  position: relative;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-item svg {
  width: 18px;
  height: 18px;
  opacity: 0.85;
}

.nav-item:hover {
  background: rgba(244, 238, 225, 0.08);
  color: var(--paper);
}

.nav-item--active {
  background: rgba(244, 238, 225, 0.12);
  color: var(--paper);
  font-weight: 700;
}

.nav-item--active::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 6px;
  bottom: 6px;
  width: 4px;
  background: var(--vermilion);
  border-radius: 0 3px 3px 0;
}

/* 进行中任务 */
.rail-section {
  padding: 14px 12px;
  border-top: 1px solid rgba(244, 238, 225, 0.14);
  overflow-y: auto;
}

.rail-heading {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: rgba(244, 238, 225, 0.45);
  padding: 0 10px 10px;
}

.rail-run {
  display: flex;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 4px;
  text-decoration: none;
  color: rgba(244, 238, 225, 0.85);
  transition: background 0.15s ease;
}

.rail-run:hover {
  background: rgba(244, 238, 225, 0.08);
}

.rail-run-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
}

.dot--researching,
.dot--synthesizing {
  background: var(--amber-bright);
  animation: pulse-ring 1.8s infinite;
}

.dot--awaiting_confirmation {
  background: var(--amber);
}

.dot--scope_frozen {
  background: var(--slate);
}

.rail-run-query {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.45;
}

.rail-run-meta {
  display: block;
  font-size: 10.5px;
  color: rgba(244, 238, 225, 0.42);
  margin-top: 3px;
}

.rail-foot {
  margin-top: auto;
  padding: 16px 22px 20px;
  border-top: 1px solid rgba(244, 238, 225, 0.14);
}

.rail-foot-row {
  font-size: 11px;
  color: rgba(244, 238, 225, 0.42);
  line-height: 1.9;
}

/* ============ 主区 ============ */
.main {
  flex: 1;
  min-width: 0;
}

.page-enter-active,
.page-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
