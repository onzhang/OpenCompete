<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRunsStore } from '../stores/runs.js'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StatusStamp from '../components/StatusStamp.vue'
import { fmtDateTime, fmtUsd, shortId } from '../utils/format.js'

const store = useRunsStore()

onMounted(() => {
  store.fetchRuns()
})

const query = ref('')
const statusFilter = ref('all')
const pendingDelete = ref(null)
const deleteBusy = ref(false)
const deleteError = ref('')
const deleteTrigger = ref(null)

const FILTERS = [
  { v: 'all', label: '全部' },
  { v: 'active', label: '进行中' },
  { v: 'completed', label: '已完成' },
  { v: 'partial', label: '部分完成' },
  { v: 'failed', label: '失败' },
  { v: 'cancelled', label: '已取消' },
]

const ACTIVE_SET = ['researching', 'synthesizing', 'awaiting_confirmation', 'scope_frozen', 'draft']

const filtered = computed(() => {
  let list = store.runs
  if (statusFilter.value === 'active') {
    list = list.filter((r) => ACTIVE_SET.includes(r.status))
  } else if (statusFilter.value !== 'all') {
    list = list.filter((r) => r.status === statusFilter.value)
  }
  const q = query.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (r) =>
        (r.query || '').toLowerCase().includes(q) ||
        r.run_id.toLowerCase().includes(q) ||
        (r.entities || []).some((e) => e.toLowerCase().includes(q)),
    )
  }
  return list
})

const countOf = computed(() => (v) => {
  if (v === 'all') return store.runs.length
  if (v === 'active') return store.runs.filter((r) => ACTIVE_SET.includes(r.status)).length
  return store.runs.filter((r) => r.status === v).length
})

function hasReport(run) {
  return ['completed', 'partial'].includes(run.status)
}

function requestDelete(run, event) {
  pendingDelete.value = run
  deleteError.value = ''
  deleteTrigger.value = event.currentTarget
}

function closeDeleteDialog() {
  if (deleteBusy.value) return
  pendingDelete.value = null
  deleteError.value = ''
}

async function confirmDelete() {
  if (!pendingDelete.value || deleteBusy.value) return
  deleteBusy.value = true
  deleteError.value = ''
  try {
    await store.deleteRun(pendingDelete.value.run_id)
    pendingDelete.value = null
  } catch (err) {
    deleteError.value = err.message
  } finally {
    deleteBusy.value = false
  }
}

const deleteDetails = computed(() => {
  const run = pendingDelete.value
  if (!run) return []
  const entities = (run.entities || []).filter(Boolean)
  return [
    entities.length ? `研究对象：${entities.join('、')}` : '研究对象：未记录',
    '将删除报告、结构化证据、执行事件与恢复记录',
    '此操作不可恢复',
  ]
})
</script>

<template>
  <div class="page">
    <header class="page-head rise-in">
      <span class="overline overline--accent">RUN ARCHIVE</span>
      <h1 class="page-title">历史<em>卷宗</em></h1>
      <p class="head-sub">
        所有研究任务的长期档案，读取自本地
        <code class="mono head-path">.opencompete-data/runs/</code>
        ，经 revision 与哈希校验。
      </p>
    </header>

    <!-- 筛选行 -->
    <div class="toolbar rise-in" style="animation-delay: 0.06s">
      <div class="filter-row">
        <button
          v-for="f in FILTERS"
          :key="f.v"
          class="filter-chip"
          :class="{ 'filter-chip--on': statusFilter === f.v }"
          @click="statusFilter = f.v"
        >
          {{ f.label }}
          <span class="filter-count mono">{{ countOf(f.v) }}</span>
        </button>
      </div>
      <div class="search">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></svg>
        <input
          v-model="query"
          class="search-input"
          placeholder="搜索研究问题、对象或 run_id…"
        />
      </div>
    </div>

    <!-- 卷宗列表 -->
    <div v-if="filtered.length" class="dossiers rise-in" style="animation-delay: 0.12s">
      <div
        v-for="(run, i) in filtered"
        :key="run.run_id"
        class="dossier sheet"
      >
        <div class="dossier-spine" :class="`spine--${run.status}`"></div>
        <div class="dossier-index mono">{{ String(i + 1).padStart(2, '0') }}</div>

        <div class="dossier-main">
          <RouterLink :to="`/runs/${run.run_id}`" class="dossier-query">{{ run.query || '（未记录研究问题）' }}</RouterLink>
          <div class="dossier-sub">
            <span class="dossier-entities">{{ (run.entities || []).join('、') }}</span>
            <span class="dossier-id mono">{{ shortId(run.run_id) }}</span>
            <span class="dossier-rev mono">scope rev.{{ run.scope_revision }} / artifact rev.{{ run.artifact_revision }}</span>
          </div>
          <div v-if="run.partial_reason || run.error" class="dossier-note" :class="{ 'dossier-note--err': run.error }">
            {{ run.error ? `[${run.error.code}] ${run.error.message}` : run.partial_reason }}
          </div>
        </div>

        <div class="dossier-facts">
          <div class="fact">
            <span class="fact-label">更新时间</span>
            <span class="fact-value mono">{{ fmtDateTime(run.updated_at) }}</span>
          </div>
          <div class="fact">
            <span class="fact-label">成本</span>
            <span class="fact-value mono">{{ fmtUsd(run.budget_spent_usd) }}</span>
          </div>
          <div class="fact" v-if="run.race_overall">
            <span class="fact-label">RACE</span>
            <span class="fact-value fact-value--score mono">{{ run.race_overall.toFixed(1) }}</span>
          </div>
        </div>

        <div class="dossier-tail">
          <StatusStamp :status="run.status" size="sm" />
          <div class="dossier-actions">
            <RouterLink :to="`/runs/${run.run_id}`" class="action">
              {{ run.status === 'awaiting_confirmation' ? '去确认' : '详情' }}
            </RouterLink>
            <RouterLink
              v-if="hasReport(run)"
              :to="`/runs/${run.run_id}/report`"
              class="action action--primary"
            >
              阅读报告
            </RouterLink>
            <button
              v-if="!ACTIVE_SET.includes(run.status)"
              class="action action--delete"
              @click="requestDelete(run, $event)"
            >
              删除
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty sheet rise-in">
      <div class="empty-art" aria-hidden="true">
        <svg viewBox="0 0 80 64" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M8 14h22l6 8h36v34H8z" />
          <path d="M8 14v-4h18l4 4" opacity="0.5" />
          <path d="M24 38h32M24 46h20" stroke-dasharray="2 4" />
        </svg>
      </div>
      <div class="empty-title">没有匹配的卷宗</div>
      <p class="empty-desc">调整筛选条件，或发起一个新的竞品研究。</p>
      <RouterLink to="/new" class="btn btn--primary">发起新研究</RouterLink>
    </div>

    <ConfirmDialog
      :open="!!pendingDelete"
      title="彻底删除卷宗"
      :subject="pendingDelete?.query || '未记录研究问题'"
      :details="deleteDetails"
      confirm-label="彻底删除"
      pending-label="正在删除卷宗…"
      :pending="deleteBusy"
      :error="deleteError"
      :return-focus="deleteTrigger"
      @cancel="closeDeleteDialog"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.head-sub {
  margin-top: 10px;
  font-size: 13.5px;
  color: var(--ink-soft);
}

.head-path {
  font-size: 12px;
  background: var(--paper-dim);
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 1px 7px;
}

/* 工具条 */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  margin: 26px 0 20px;
  padding-bottom: 18px;
  border-bottom: 2px solid var(--ink);
}

.filter-row {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  font-weight: 600;
  padding: 5px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-soft);
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

.filter-count {
  font-size: 10px;
  opacity: 0.65;
}

.search {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 300px;
  padding: 8px 14px;
  background: var(--paper-raised);
  border: 1.5px solid var(--line-strong);
  border-radius: var(--radius);
  color: var(--ink-faint);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.search:focus-within {
  border-color: var(--ink);
  box-shadow: 3px 3px 0 rgba(28, 33, 29, 0.16);
}

.search-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 13.5px;
  color: var(--ink);
}

.search-input:focus {
  outline: none;
}

/* 卷宗列表 */
.dossiers {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 17px 22px 17px 0;
  position: relative;
  overflow: hidden;
  transition: transform 0.14s ease, box-shadow 0.14s ease, border-color 0.14s ease;
}

.dossier:hover {
  transform: translateX(3px);
  border-color: var(--ink);
}

/* 卷宗脊背色条 */
.dossier-spine {
  width: 6px;
  align-self: stretch;
  flex-shrink: 0;
  border-radius: 0 3px 3px 0;
}

.spine--completed { background: var(--forest); }
.spine--partial { background: var(--amber); }
.spine--failed { background: var(--vermilion); }
.spine--cancelled { background: var(--line-strong); }
.spine--awaiting_confirmation { background: var(--amber); }
.spine--researching,
.spine--synthesizing {
  background: repeating-linear-gradient(
    -45deg,
    var(--amber-bright) 0 6px,
    var(--amber-wash) 6px 12px
  );
}
.spine--scope_frozen,
.spine--draft { background: var(--slate); }

.dossier-index {
  font-size: 13px;
  color: var(--ink-ghost);
  width: 24px;
  flex-shrink: 0;
}

.dossier-main {
  flex: 1;
  min-width: 0;
}

.dossier-query {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 700;
  line-height: 1.5;
  text-decoration: none;
  color: inherit;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.12s ease;
}

.dossier-query:hover {
  color: var(--vermilion);
}

.dossier-sub {
  display: flex;
  align-items: baseline;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 5px;
  font-size: 12px;
  color: var(--ink-faint);
}

.dossier-id,
.dossier-rev {
  font-size: 11px;
  color: var(--ink-ghost);
}

.dossier-note {
  margin-top: 7px;
  font-size: 12px;
  color: var(--amber);
  background: var(--amber-wash);
  border-radius: 3px;
  padding: 3px 10px;
  display: inline-block;
}

.dossier-note--err {
  color: var(--vermilion-deep);
  background: var(--vermilion-wash);
}

.dossier-facts {
  display: flex;
  gap: 26px;
  flex-shrink: 0;
}

.fact {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 62px;
}

.fact-label {
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--ink-ghost);
}

.fact-value {
  font-size: 12.5px;
  color: var(--ink-soft);
}

.fact-value--score {
  color: var(--forest);
  font-weight: 600;
  font-size: 15px;
}

.dossier-tail {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 9px;
  flex-shrink: 0;
  min-width: 128px;
}

.dossier-actions {
  display: flex;
  gap: 8px;
}

.action {
  font-size: 12px;
  font-weight: 700;
  padding: 4px 13px;
  border: 1.5px solid var(--line-strong);
  border-radius: 3px;
  text-decoration: none;
  color: var(--ink-soft);
  transition: all 0.12s ease;
}

.action:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.action--primary {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--paper);
}

.action--primary:hover {
  background: var(--vermilion);
  border-color: var(--vermilion);
  color: #fdf6ee;
}

.action--delete {
  background: transparent;
  border-color: transparent;
  color: var(--ink-faint);
}

.action--delete:hover {
  border-color: var(--vermilion);
  background: var(--vermilion-wash);
  color: var(--vermilion-deep);
}

/* 空状态 */
.empty {
  padding: 64px 40px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.empty-art {
  width: 92px;
  color: var(--line-strong);
  margin-bottom: 18px;
}

.empty-title {
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 700;
}

.empty-desc {
  font-size: 13px;
  color: var(--ink-faint);
  margin: 8px 0 22px;
}

@media (max-width: 1080px) {
  .dossier {
    flex-wrap: wrap;
    padding-right: 18px;
  }
  .dossier-facts {
    order: 4;
    width: 100%;
    padding-left: 30px;
  }
  .dossier-tail {
    align-items: flex-start;
  }
}
</style>
