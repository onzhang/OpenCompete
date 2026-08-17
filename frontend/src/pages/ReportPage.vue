<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import { useRunsStore } from '../stores/runs.js'
import StatusStamp from '../components/StatusStamp.vue'
import {
  CLAIM_STATUS_LABELS,
  LEVEL_LABELS,
  fmtDateTime,
  fmtDimension,
  fmtUsd,
  shortId,
} from '../utils/format.js'

const route = useRoute()
const store = useRunsStore()

const runId = computed(() => route.params.id)
const run = computed(() => store.runById(runId.value) || store.detailFor(runId.value))
const bundle = computed(() => store.bundleFor(runId.value))

const loading = ref(true)
const loadError = ref(null)

onMounted(async () => {
  window.addEventListener('scroll', updateBackToTop, { passive: true })
  updateBackToTop()
  try {
    await Promise.all([
      store.fetchRunDetail(runId.value).catch(() => {}),
      store.fetchBundle(runId.value),
    ])
  } catch (err) {
    loadError.value = err.message
  } finally {
    loading.value = false
  }
  buildToc()
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', updateBackToTop)
})

const tab = ref('report')
const tabs = [
  { key: 'report', label: '报告正文' },
  { key: 'evidence', label: '证据链' },
  { key: 'matrix', label: '对比矩阵' },
]

/* ---------- 报告渲染与引用 ---------- */
const articleRef = ref(null)
const toc = ref([])
const activeSource = ref(null)
const showBackToTop = ref(false)

const sourceByRef = computed(() => {
  const map = {}
  for (const s of bundle.value?.sources || []) {
    if (s.ref != null) map[s.ref] = s
  }
  return map
})

const renderedHtml = computed(() => {
  if (!bundle.value) return ''
  let html = marked.parse(bundle.value.report.markdown)
  // 正文内联引用 [1][2] → 可点击上标（编号与来源的 ref 字段对应）
  html = html.replace(
    /\[(\d+)\]/g,
    '<sup class="cite" data-ref="$1" role="button" tabindex="0">[$1]</sup>',
  )
  return html
})

async function buildToc() {
  await nextTick()
  const rootEl = articleRef.value
  if (!rootEl) return
  const heads = rootEl.querySelectorAll('h2, h3')
  const list = []
  heads.forEach((h, i) => {
    h.id = `sec-${i}`
    list.push({ id: h.id, text: h.textContent, level: h.tagName === 'H2' ? 2 : 3 })
  })
  toc.value = list
}

function onArticleClick(e) {
  const cite = e.target.closest('.cite')
  if (cite) {
    const src = sourceByRef.value[cite.dataset.ref]
    if (src) activeSource.value = src
  }
}

function scrollTo(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function updateBackToTop() {
  showBackToTop.value = window.scrollY > 560
}

function backToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

watch(renderedHtml, buildToc)
watch(tab, (t) => {
  if (t === 'report') buildToc()
})

/* ---------- 证据链 ---------- */
const claimFilter = ref('all')
const entityFilter = ref('all')

const entityName = computed(() => (id) => {
  const e = bundle.value?.scope.entities.find((x) => x.entity_id === id)
  return e ? e.name : id
})

const filteredClaims = computed(() => {
  let claims = bundle.value?.claims || []
  if (claimFilter.value !== 'all') claims = claims.filter((c) => c.status === claimFilter.value)
  if (entityFilter.value !== 'all') claims = claims.filter((c) => c.entity_id === entityFilter.value)
  return claims
})

const evidenceById = computed(() => {
  const map = {}
  for (const e of bundle.value?.evidence || []) map[e.evidence_id] = e
  return map
})

const sourceById = computed(() => {
  const map = {}
  for (const s of bundle.value?.sources || []) map[s.source_id] = s
  return map
})

const expandedClaim = ref(null)

const claimStats = computed(() => {
  const claims = bundle.value?.claims || []
  return {
    supported: claims.filter((c) => c.status === 'supported').length,
    conflicted: claims.filter((c) => c.status === 'conflicted').length,
    unknown: claims.filter((c) => c.status === 'unknown').length,
  }
})

/* ---------- 对比矩阵 ---------- */
const claimById = computed(() => {
  const map = {}
  for (const c of bundle.value?.claims || []) map[c.claim_id] = c
  return map
})

function exportMarkdown() {
  if (!bundle.value) return
  const blob = new Blob([bundle.value.report.markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `opencompete-report-${shortId(runId.value)}.md`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="report-page" v-if="run && bundle">
    <!-- 元信息条 -->
    <div class="meta-bar">
      <RouterLink :to="`/runs/${run.run_id}`" class="meta-back">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 8H3M7 4 3 8l4 4" /></svg>
        卷宗详情
      </RouterLink>
      <span class="meta-sep"></span>
      <span class="mono meta-id">{{ shortId(run.run_id) }}</span>
      <StatusStamp :status="run.status" size="sm" />
      <span class="meta-item">发布于 {{ fmtDateTime(bundle.created_at) }}</span>
      <span class="meta-item">{{ bundle.sources.length }} 个来源 · {{ bundle.claims.length }} 条结论</span>
      <span class="meta-item">成本 {{ fmtUsd(bundle.budget.spent.cost_usd) }}</span>
      <span v-if="run.race_overall" class="meta-score mono">RACE {{ run.race_overall.toFixed(1) }}</span>
      <button class="btn btn--ghost meta-export" @click="exportMarkdown">
        <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v8M4.5 6.5 8 10l3.5-3.5M3 13h10" /></svg>
        导出 report.md
      </button>
    </div>

    <Transition name="back-to-top">
      <button
        v-if="showBackToTop"
        class="back-to-top"
        :class="{ 'back-to-top--raised': activeSource }"
        type="button"
        title="返回报告卷首"
        aria-label="返回报告卷首"
        @click="backToTop"
      >
        <span class="back-to-top-mark" aria-hidden="true">↑</span>
        <span class="back-to-top-label">卷首</span>
      </button>
    </Transition>

    <!-- Tab 切换 -->
    <div class="tabbar">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ 'tab--on': tab === t.key }"
        @click="tab = t.key"
      >
        {{ t.label }}
        <span v-if="t.key === 'evidence'" class="tab-count mono">{{ bundle.claims.length }}</span>
        <span v-if="t.key === 'matrix'" class="tab-count mono">{{ bundle.comparisons.length }}</span>
      </button>
    </div>

    <!-- ============ 报告正文 ============ -->
    <div v-if="tab === 'report'" class="report-layout">
      <aside class="toc">
        <div class="toc-head overline">目录</div>
        <button
          v-for="item in toc"
          :key="item.id"
          class="toc-item"
          :class="{ 'toc-item--sub': item.level === 3 }"
          @click="scrollTo(item.id)"
        >
          {{ item.text }}
        </button>
      </aside>

      <article
        ref="articleRef"
        class="article sheet"
        @click="onArticleClick"
        v-html="renderedHtml"
      ></article>

      <!-- 来源浮层 -->
      <Transition name="drawer">
        <aside v-if="activeSource" class="source-drawer pressed" @click.stop>
          <div class="drawer-head">
            <span class="drawer-ref mono">[{{ activeSource.ref }}]</span>
            <button class="drawer-close" @click="activeSource = null" aria-label="关闭">×</button>
          </div>
          <div class="drawer-title">{{ activeSource.title }}</div>
          <div class="drawer-pub">{{ activeSource.publisher }} · 访问于 {{ fmtDateTime(activeSource.accessed_at) }}</div>
          <a class="drawer-url mono" :href="activeSource.url" target="_blank" rel="noopener">{{ activeSource.url }}</a>
          <div class="drawer-note">该来源已通过公网安全边界校验（仅公开 HTTP/HTTPS）。</div>
        </aside>
      </Transition>
    </div>

    <!-- ============ 证据链 ============ -->
    <div v-else-if="tab === 'evidence'" class="evidence-layout">
      <div class="chain-legend sheet">
        <div class="chain-flow">
          <span class="chain-node">Source<i>来源</i></span>
          <span class="chain-arrow">→</span>
          <span class="chain-node">Evidence<i>证据</i></span>
          <span class="chain-arrow">→</span>
          <span class="chain-node">Claim<i>结论</i></span>
        </div>
        <div class="chain-stats">
          <span class="chain-stat"><b class="mono stat-ok">{{ claimStats.supported }}</b> 有证据支持</span>
          <span class="chain-stat"><b class="mono stat-warn">{{ claimStats.conflicted }}</b> 证据冲突</span>
          <span class="chain-stat"><b class="mono stat-mute">{{ claimStats.unknown }}</b> 未知</span>
        </div>
      </div>

      <div class="filters">
        <div class="filter-group">
          <button
            v-for="opt in [
              { v: 'all', label: '全部状态' },
              { v: 'supported', label: '有证据支持' },
              { v: 'conflicted', label: '证据冲突' },
              { v: 'unknown', label: '未知' },
            ]"
            :key="opt.v"
            class="filter-chip"
            :class="{ 'filter-chip--on': claimFilter === opt.v }"
            @click="claimFilter = opt.v"
          >
            {{ opt.label }}
          </button>
        </div>
        <div class="filter-group">
          <button
            class="filter-chip"
            :class="{ 'filter-chip--on': entityFilter === 'all' }"
            @click="entityFilter = 'all'"
          >
            全部对象
          </button>
          <button
            v-for="e in bundle.scope.entities"
            :key="e.entity_id"
            class="filter-chip"
            :class="{ 'filter-chip--on': entityFilter === e.entity_id }"
            @click="entityFilter = e.entity_id"
          >
            {{ e.name }}
          </button>
        </div>
      </div>

      <div class="claims">
        <div
          v-for="claim in filteredClaims"
          :key="claim.claim_id"
          class="claim sheet"
          :class="{ 'claim--open': expandedClaim === claim.claim_id }"
        >
          <button
            class="claim-row"
            @click="expandedClaim = expandedClaim === claim.claim_id ? null : claim.claim_id"
          >
            <span class="claim-status" :class="`cs--${claim.status}`">{{ CLAIM_STATUS_LABELS[claim.status] }}</span>
            <span class="claim-text">{{ claim.text }}</span>
            <span class="claim-meta">
              <b>{{ entityName(claim.entity_id) }}</b>
              {{ LEVEL_LABELS[claim.level] }} · {{ fmtDimension(claim.dimension) }}
            </span>
            <svg class="claim-chev" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6 4 4 4-4" /></svg>
          </button>

          <div v-if="expandedClaim === claim.claim_id" class="claim-detail">
            <div v-if="claim.uncertainty" class="claim-uncertain">
              <b>不确定性说明</b>{{ claim.uncertainty }}
            </div>
            <div
              v-for="eid in claim.evidence_ids"
              :key="eid"
              class="evd"
            >
              <blockquote class="evd-quote">{{ evidenceById[eid]?.excerpt }}</blockquote>
              <div class="evd-src" v-if="sourceById[evidenceById[eid]?.source_id]">
                <span class="evd-ref mono">{{ sourceById[evidenceById[eid].source_id].ref }}</span>
                <a :href="sourceById[evidenceById[eid].source_id].url" target="_blank" rel="noopener">
                  {{ sourceById[evidenceById[eid].source_id].title }}
                </a>
                <span class="evd-loc" v-if="evidenceById[eid]?.location">{{ evidenceById[eid].location }}</span>
              </div>
            </div>
            <div v-if="!claim.evidence_ids.length && !claim.uncertainty" class="evd-none">
              该结论未引用证据。
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 对比矩阵 ============ -->
    <div v-else class="matrix-layout">
      <div v-for="cmp in bundle.comparisons" :key="cmp.comparison_id" class="cmp sheet">
        <div class="cmp-head">
          <div>
            <div class="overline overline--accent">{{ LEVEL_LABELS[cmp.level] }}</div>
            <h3 class="cmp-title">{{ fmtDimension(cmp.dimension) }}</h3>
          </div>
          <div class="cmp-entities">
            <span v-for="eid in cmp.entity_ids" :key="eid" class="cmp-entity">{{ entityName(eid) }}</span>
          </div>
        </div>
        <div class="cmp-cols">
          <div class="cmp-col">
            <div class="cmp-col-head cmp-col-head--diff">关键差异</div>
            <ul><li v-for="d in cmp.differences" :key="d">{{ d }}</li></ul>
          </div>
          <div class="cmp-col">
            <div class="cmp-col-head cmp-col-head--common">共性</div>
            <ul><li v-for="c in cmp.commonalities" :key="c">{{ c }}</li></ul>
          </div>
          <div class="cmp-col" v-if="cmp.gaps.length">
            <div class="cmp-col-head cmp-col-head--gap">信息缺口</div>
            <ul><li v-for="g in cmp.gaps" :key="g">{{ g }}</li></ul>
          </div>
        </div>
        <div class="cmp-claims" v-if="cmp.claim_ids.length">
          <span class="cmp-claims-label">依据结论：</span>
          <span v-for="cid in cmp.claim_ids" :key="cid" class="cmp-claim-chip" :title="claimById[cid]?.text">
            {{ claimById[cid]?.text.slice(0, 24) }}…
          </span>
        </div>
      </div>
    </div>

    <!-- 来源清单（正文 tab 下） -->
    <template v-if="tab === 'report'">
      <section class="srcs">
        <div class="rule-heading"><span class="overline overline--accent">来源清单</span></div>
        <div class="src-list">
          <a
            v-for="s in bundle.sources"
            :key="s.source_id"
            class="src-item"
            :href="s.url"
            target="_blank"
            rel="noopener"
          >
            <span class="src-ref mono">{{ s.ref != null ? `[${s.ref}]` : '—' }}</span>
            <span class="src-body">
              <span class="src-title">{{ s.title }}<i v-if="s.ref == null" class="src-unref">未在报告中引用</i></span>
              <span class="src-meta mono">{{ s.publisher }} · {{ s.url }}</span>
            </span>
          </a>
        </div>
      </section>
    </template>
  </div>

  <div v-else class="page">
    <div v-if="loading" class="sheet not-ready">
      <h2>正在加载报告…</h2>
      <p>从本地档案读取结构化产物并校验完整性。</p>
    </div>
    <div v-else class="sheet not-ready">
      <h2>报告尚未就绪</h2>
      <p>{{ loadError || '该任务还没有可查看的已发布报告。任务保存在本地，可稍后恢复或等待完成。' }}</p>
      <RouterLink :to="`/runs/${runId}`" class="btn btn--ink">查看卷宗状态</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.report-page {
  padding: 26px 42px 80px;
  max-width: 1240px;
  margin: 0 auto;
}

/* 元信息条 */
.meta-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding-bottom: 16px;
  border-bottom: 3px double var(--line-ink);
  margin-bottom: 18px;
}

.meta-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  color: var(--ink-soft);
}

.meta-back:hover {
  color: var(--vermilion);
}

.meta-sep {
  width: 1px;
  height: 16px;
  background: var(--line-strong);
}

.meta-id {
  font-size: 12px;
  color: var(--ink-faint);
}

.meta-item {
  font-size: 12.5px;
  color: var(--ink-faint);
}

.meta-score {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--forest);
  background: var(--forest-wash);
  padding: 2px 10px;
  border-radius: 3px;
  border: 1px solid var(--forest);
}

.meta-export {
  margin-left: auto;
  padding: 6px 14px;
  font-size: 12.5px;
}

/* Tabs */
.tabbar {
  display: flex;
  gap: 4px;
  margin-bottom: 24px;
}

.tab {
  padding: 9px 20px;
  font-size: 13.5px;
  font-weight: 600;
  border: 1.5px solid var(--line-strong);
  border-bottom-width: 3px;
  border-radius: var(--radius) var(--radius) 0 0;
  background: var(--paper-dim);
  color: var(--ink-faint);
  transition: all 0.15s ease;
}

.tab:hover {
  color: var(--ink);
}

.tab--on {
  background: var(--paper-raised);
  color: var(--ink);
  border-color: var(--ink);
  border-bottom-color: var(--vermilion);
}

.tab-count {
  font-size: 10.5px;
  margin-left: 5px;
  color: var(--ink-ghost);
}

/* 报告布局 */
.report-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 28px;
  align-items: start;
  position: relative;
}

.toc {
  position: sticky;
  top: 26px;
  max-height: calc(100vh - 60px);
  overflow-y: auto;
  padding-right: 4px;
}

.toc-head {
  padding-bottom: 10px;
  border-bottom: 2px solid var(--ink);
  margin-bottom: 8px;
}

.toc-item {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--ink-soft);
  padding: 6px 2px;
  border-bottom: 1px dotted var(--line);
  transition: color 0.12s ease;
}

.toc-item:hover {
  color: var(--vermilion);
}

.toc-item--sub {
  padding-left: 16px;
  font-size: 12px;
  color: var(--ink-faint);
}

/* 印刷正文 */
.article {
  padding: 52px 58px 60px;
  font-size: 15px;
  line-height: 1.95;
  color: #232823;
}

.article :deep(h1) {
  font-family: var(--font-serif);
  font-size: 30px;
  font-weight: 900;
  line-height: 1.45;
  padding-bottom: 22px;
  margin-bottom: 30px;
  border-bottom: 3px double var(--line-ink);
}

.article :deep(h2) {
  font-family: var(--font-serif);
  font-size: 21.5px;
  font-weight: 700;
  margin: 44px 0 16px;
  padding-left: 14px;
  border-left: 4px solid var(--vermilion);
  line-height: 1.5;
}

.article :deep(h3) {
  font-family: var(--font-serif);
  font-size: 16.5px;
  font-weight: 700;
  margin: 30px 0 12px;
}

.article :deep(p) {
  margin: 0 0 16px;
}

.article :deep(strong) {
  font-weight: 700;
  background: linear-gradient(transparent 62%, rgba(194, 64, 28, 0.16) 62%);
}

.article :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 20px 0 24px;
  font-size: 13.5px;
  line-height: 1.6;
}

.article :deep(th) {
  font-family: var(--font-serif);
  font-weight: 700;
  text-align: left;
  padding: 9px 13px;
  border-top: 2px solid var(--ink);
  border-bottom: 1.5px solid var(--ink);
  background: var(--paper-dim);
}

.article :deep(td) {
  padding: 9px 13px;
  border-bottom: 1px solid var(--line);
}

.article :deep(ol),
.article :deep(ul) {
  padding-left: 26px;
  margin: 0 0 16px;
}

.article :deep(li) {
  margin-bottom: 7px;
}

.article :deep(.cite) {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--vermilion);
  background: var(--vermilion-wash);
  border: 1px solid rgba(194, 64, 28, 0.4);
  border-radius: 3px;
  padding: 0 4px;
  margin: 0 2px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.12s ease;
}

.article :deep(.cite:hover) {
  background: var(--vermilion);
  color: var(--paper-raised);
}

/* 来源浮层 */
.source-drawer {
  position: fixed;
  right: 34px;
  bottom: 34px;
  width: 380px;
  max-width: calc(100vw - 60px);
  padding: 20px 24px;
  z-index: 60;
  background: var(--paper-raised);
}

.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.drawer-ref {
  font-size: 12px;
  font-weight: 600;
  color: var(--vermilion);
  border: 1px solid var(--vermilion);
  border-radius: 3px;
  padding: 1px 8px;
}

.drawer-close {
  border: none;
  background: transparent;
  font-size: 22px;
  line-height: 1;
  color: var(--ink-faint);
}

.drawer-close:hover {
  color: var(--vermilion);
}

.drawer-title {
  font-family: var(--font-serif);
  font-size: 15.5px;
  font-weight: 700;
  line-height: 1.5;
}

.drawer-pub {
  font-size: 12px;
  color: var(--ink-faint);
  margin: 6px 0 10px;
}

.drawer-url {
  display: block;
  font-size: 11.5px;
  color: var(--slate);
  word-break: break-all;
  text-decoration: none;
  border-bottom: 1px dotted var(--slate);
  padding-bottom: 2px;
}

.drawer-url:hover {
  color: var(--vermilion);
}

.drawer-note {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dotted var(--line-strong);
  font-size: 11px;
  color: var(--ink-ghost);
}

.drawer-enter-active,
.drawer-leave-active {
  transition: all 0.25s cubic-bezier(0.22, 0.8, 0.3, 1);
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
  transform: translateY(16px);
}

/* 证据链 */
.chain-legend {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  padding: 16px 24px;
  margin-bottom: 18px;
}

.chain-flow {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chain-node {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  border: 1.5px solid var(--ink);
  border-radius: 3px;
  padding: 4px 12px;
  background: var(--paper-dim);
}

.chain-node i {
  font-style: normal;
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-faint);
  margin-left: 6px;
}

.chain-arrow {
  color: var(--vermilion);
  font-weight: 700;
}

.chain-stats {
  display: flex;
  gap: 20px;
  font-size: 12.5px;
  color: var(--ink-soft);
}

.chain-stat b {
  font-size: 16px;
  margin-right: 4px;
}

.stat-ok { color: var(--forest); }
.stat-warn { color: var(--amber); }
.stat-mute { color: var(--ink-faint); }

.filters {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.filter-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.filter-chip {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 13px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-faint);
  transition: all 0.12s ease;
}

.filter-chip:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.filter-chip--on {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--paper);
}

.claims {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.claim-row {
  display: grid;
  grid-template-columns: 92px 1fr auto 18px;
  align-items: center;
  gap: 16px;
  width: 100%;
  padding: 14px 18px;
  border: none;
  background: transparent;
  text-align: left;
}

.claim-status {
  font-size: 11px;
  font-weight: 700;
  text-align: center;
  padding: 3px 0;
  border: 1.5px solid currentColor;
  border-radius: 3px;
  letter-spacing: 0.05em;
}

.cs--supported { color: var(--forest); }
.cs--conflicted { color: var(--amber); }
.cs--unknown { color: var(--ink-faint); }

.claim-text {
  font-size: 14px;
  font-weight: 500;
  line-height: 1.6;
}

.claim-meta {
  font-size: 11.5px;
  color: var(--ink-faint);
  text-align: right;
  white-space: nowrap;
}

.claim-meta b {
  display: block;
  font-size: 12.5px;
  color: var(--ink-soft);
}

.claim-chev {
  color: var(--ink-ghost);
  transition: transform 0.2s ease;
}

.claim--open .claim-chev {
  transform: rotate(180deg);
}

.claim-detail {
  padding: 4px 18px 18px 126px;
}

.claim-uncertain {
  background: var(--paper-dim);
  border-left: 3px solid var(--ink-faint);
  padding: 10px 14px;
  font-size: 12.5px;
  color: var(--ink-soft);
  border-radius: var(--radius);
  margin-bottom: 10px;
}

.claim-uncertain b {
  display: block;
  font-size: 11px;
  color: var(--ink-faint);
  margin-bottom: 3px;
}

.evd {
  margin-bottom: 12px;
}

.evd-quote {
  font-family: var(--font-serif);
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--ink-soft);
  background: var(--paper-dim);
  border-left: 3px solid var(--vermilion);
  padding: 12px 16px;
  border-radius: var(--radius);
}

.evd-src {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 6px;
  font-size: 12px;
  flex-wrap: wrap;
}

.evd-ref {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--vermilion);
  border: 1px solid var(--vermilion);
  border-radius: 3px;
  padding: 0 6px;
}

.evd-src a {
  color: var(--slate);
  text-decoration: none;
  border-bottom: 1px dotted var(--slate);
}

.evd-src a:hover {
  color: var(--vermilion);
}

.evd-loc {
  color: var(--ink-ghost);
}

.evd-none {
  font-size: 12.5px;
  color: var(--ink-ghost);
}

/* 对比矩阵 */
.matrix-layout {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.cmp {
  padding: 22px 28px;
}

.cmp-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 20px;
  padding-bottom: 14px;
  border-bottom: 2px solid var(--ink);
  margin-bottom: 16px;
}

.cmp-title {
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 900;
  margin-top: 4px;
}

.cmp-entities {
  display: flex;
  gap: 8px;
}

.cmp-entity {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 11px;
  border: 1.5px solid var(--ink);
  border-radius: 999px;
}

.cmp-cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}

.cmp-col-head {
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 4px 10px;
  border-radius: 3px;
  display: inline-block;
  margin-bottom: 10px;
}

.cmp-col-head--diff {
  background: var(--vermilion-wash);
  color: var(--vermilion-deep);
}

.cmp-col-head--common {
  background: var(--forest-wash);
  color: var(--forest);
}

.cmp-col-head--gap {
  background: var(--amber-wash);
  color: var(--amber);
}

.cmp-col ul {
  list-style: none;
}

.cmp-col li {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-soft);
  padding: 7px 0 7px 16px;
  border-bottom: 1px dotted var(--line);
  position: relative;
}

.cmp-col li::before {
  content: '·';
  position: absolute;
  left: 2px;
  color: var(--vermilion);
  font-weight: 900;
}

.cmp-claims {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cmp-claims-label {
  font-size: 11.5px;
  color: var(--ink-faint);
}

.cmp-claim-chip {
  font-size: 11.5px;
  color: var(--ink-soft);
  background: var(--paper-dim);
  border: 1px solid var(--line-strong);
  border-radius: 3px;
  padding: 2px 9px;
  cursor: help;
}

/* 来源 */
.srcs {
  margin-top: 34px;
}

.src-list {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.src-item {
  display: flex;
  gap: 12px;
  padding: 12px 15px;
  background: var(--paper-raised);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease, transform 0.12s ease;
  min-width: 0;
}

.src-item:hover {
  border-color: var(--ink);
  transform: translateY(-1px);
}

.src-ref {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--vermilion);
  border: 1px solid var(--vermilion);
  border-radius: 3px;
  padding: 1px 7px;
  height: fit-content;
}

.src-body {
  min-width: 0;
}

.src-title {
  display: block;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}

.src-unref {
  font-style: normal;
  font-weight: 500;
  font-size: 11px;
  color: var(--ink-ghost);
  margin-left: 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 1px 8px;
}

.src-meta {
  display: block;
  font-size: 10.5px;
  color: var(--ink-ghost);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.not-ready {
  padding: 60px;
  text-align: center;
}

.not-ready h2 {
  font-family: var(--font-serif);
}

.not-ready p {
  color: var(--ink-faint);
  margin: 12px 0 22px;
}

.back-to-top {
  position: fixed;
  right: 28px;
  bottom: 26px;
  z-index: 55;
  display: grid;
  justify-items: center;
  gap: 2px;
  width: 54px;
  min-height: 54px;
  padding: 6px 5px 5px;
  border: 1.5px solid var(--ink);
  border-radius: var(--radius);
  background: var(--paper-raised);
  box-shadow: var(--shadow-press-sm), var(--shadow-paper);
  color: var(--ink);
  cursor: pointer;
  transition: transform 0.12s ease, background 0.15s ease, box-shadow 0.12s ease;
}

.back-to-top:hover {
  background: var(--vermilion-wash);
  transform: translate(-1px, -1px);
  box-shadow: 4px 4px 0 var(--ink), var(--shadow-paper);
}

.back-to-top:active {
  transform: translate(2px, 2px);
  box-shadow: 1px 1px 0 var(--ink);
}

.back-to-top--raised {
  bottom: min(460px, calc(100vh - 110px));
}

.back-to-top-mark {
  color: var(--vermilion);
  font-family: var(--font-serif);
  font-size: 23px;
  font-weight: 700;
  line-height: 0.9;
}

.back-to-top-label {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.back-to-top-enter-active,
.back-to-top-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.back-to-top-enter-from,
.back-to-top-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

@media (max-width: 1080px) {
  .report-layout {
    grid-template-columns: 1fr;
  }
  .toc {
    position: static;
    max-height: none;
  }
  .article {
    padding: 34px 28px;
  }
  .claim-row {
    grid-template-columns: 80px 1fr 18px;
  }
  .claim-meta {
    display: none;
  }
  .claim-detail {
    padding-left: 18px;
  }
  .src-list {
    grid-template-columns: 1fr;
  }
  .back-to-top {
    right: 14px;
    bottom: 14px;
    width: 48px;
    min-height: 48px;
  }
  .back-to-top--raised {
    bottom: min(460px, calc(100vh - 96px));
  }
}
</style>
