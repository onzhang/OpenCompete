<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRunsStore } from '../stores/runs.js'
import StatusStamp from '../components/StatusStamp.vue'
import PipelineTrack from '../components/PipelineTrack.vue'
import EventFeed from '../components/EventFeed.vue'
import BudgetMeter from '../components/BudgetMeter.vue'
import TagInput from '../components/TagInput.vue'
import { LEVEL_LABELS, fmtDateTime, shortId } from '../utils/format.js'

const route = useRoute()
const router = useRouter()
const store = useRunsStore()

const runId = computed(() => route.params.id)
const detail = computed(() => store.detailFor(runId.value))
const run = computed(() => detail.value || store.runById(runId.value))
const isLiveRun = computed(() => store.live.runId === runId.value)
const live = computed(() => store.live)
const bundle = computed(() => store.bundleFor(runId.value))

const loading = ref(true)
const loadError = ref(null)
const actionBusy = ref(false)
const actionError = ref(null)

const copied = ref(false)
function copyId() {
  navigator.clipboard?.writeText(runId.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1400)
}

/* ---------- 挂载：拉详情，按状态开直播 / 取报告 ---------- */
onMounted(async () => {
  try {
    const d = await store.fetchRunDetail(runId.value)
    // draft 期间候选发现与范围规划在后台执行，同样订阅事件流直播。
    store.maybeStartLive(d)
    if (['completed', 'partial'].includes(d.status)) {
      store.fetchBundle(runId.value).catch(() => {})
    }
  } catch (err) {
    loadError.value = err.message
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  store.stopLive()
})

/* ---------- 候选选择 ---------- */
const candidates = computed(() => detail.value?.candidates || [])
const picks = reactive({})

const candidateGroups = computed(() => {
  const groups = new Map()
  for (const candidate of candidates.value) {
    const label = candidate.category?.trim() || '其他已核验候选'
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push(candidate)
  }
  return [...groups.entries()].map(([label, items]) => ({ label, items }))
})

const candidateSourceCount = computed(
  () => new Set(candidates.value.map((candidate) => candidate.source_url)).size,
)

watch(
  candidates,
  (list) => {
    for (const c of list) {
      if (!(c.candidate_id in picks)) {
        picks[c.candidate_id] = { selected: false, name: c.entity.name }
      }
    }
  },
  { immediate: true },
)

const selectedCount = computed(
  () => candidates.value.filter((c) => picks[c.candidate_id]?.selected).length,
)
const namesValid = computed(() =>
  candidates.value.every(
    (c) => !picks[c.candidate_id]?.selected || picks[c.candidate_id].name.trim(),
  ),
)

async function submitCandidates() {
  if (!namesValid.value || actionBusy.value) return
  const choices = candidates.value
    .filter((c) => picks[c.candidate_id]?.selected)
    .map((c) => ({
      candidate_id: c.candidate_id,
      entity_name: picks[c.candidate_id].name.trim(),
    }))
  await runAction(() => store.submitCandidateSelection(runId.value, choices))
}

async function rejectAllCandidates() {
  await runAction(() => store.submitCandidateSelection(runId.value, []))
}

/* ---------- 范围确认 ---------- */
const pendingScope = computed(() => detail.value?.pending_scope || null)

async function confirmScope(accepted) {
  const ok = await runAction(() =>
    store.submitScopeConfirmation(runId.value, accepted, pendingScope.value),
  )
  if (ok && !accepted) router.push('/runs')
}

async function runAction(fn) {
  if (actionBusy.value) return false
  actionBusy.value = true
  actionError.value = null
  let failure = null
  try {
    await fn()
    return true
  } catch (err) {
    failure = err
    actionError.value = err.message
  } finally {
    actionBusy.value = false
  }

  // 操作超时或冲突后只读取 Checkpoint 投影做对账；绝不自动重试确认，
  // 更不能自动调用 resume。actionBusy 已先释放，页面不会永久锁死。
  try {
    const latest = await store.fetchRunDetail(runId.value)
    if (failure?.code === 'request_timeout') {
      if (latest.active && !latest.pending_confirmation) {
        actionError.value = '请求结果未及时返回，但任务已经进入执行链路，事件流将继续同步进度。'
        store.startLive(runId.value, { after: 0 })
      } else if (latest.pending_confirmation) {
        actionError.value = '请求已超时，任务仍在等待你的确认；核对内容后可重新提交。'
      } else if (latest.stale && latest.recoverable) {
        actionError.value = '请求已超时，任务当前未在执行；请使用“恢复研究”显式继续。'
      } else {
        actionError.value = `请求已超时；只读核对结果为“${latest.status}”。`
      }
    }
  } catch {
    // 保留原始动作错误；只读对账失败不触发任何额外执行动作。
  }
  return false
}

async function resumeResearch() {
  await runAction(() => store.resumeRun(runId.value))
}

/* ---------- 停止研究（两段式确认，终态不可恢复） ---------- */
const cancelArmed = ref(false)
let cancelArmTimer = null

function armCancel() {
  cancelArmed.value = true
  clearTimeout(cancelArmTimer)
  cancelArmTimer = setTimeout(() => (cancelArmed.value = false), 3000)
}

async function stopResearch() {
  clearTimeout(cancelArmTimer)
  cancelArmed.value = false
  await runAction(() => store.cancelRun(runId.value))
}

async function reloadDetail() {
  if (actionBusy.value) return
  actionBusy.value = true
  actionError.value = null
  try {
    await store.fetchRunDetail(runId.value)
  } catch (err) {
    actionError.value = err.message
  } finally {
    actionBusy.value = false
  }
}

const viewMode = computed(() => {
  const r = run.value
  if (!r) return 'missing'
  const d = detail.value
  if (d?.pending_confirmation === true) {
    if (d.confirmation_kind === 'candidate_selection' && candidates.value.length) {
      return 'candidates'
    }
    if (d.confirmation_kind === 'scope_confirmation' && pendingScope.value) return 'scope'
    return 'confirmation_unavailable'
  }
  if (r.status === 'awaiting_confirmation') return 'confirmation_unavailable'
  if (['researching', 'synthesizing', 'scope_frozen', 'draft'].includes(r.status)) return 'live'
  if (['completed', 'partial'].includes(r.status)) return 'done'
  if (r.status === 'failed') return 'failed'
  if (r.status === 'cancelled') return 'cancelled'
  return 'live'
})

const isStale = computed(() => !!detail.value?.stale && !detail.value?.active)
const isRecoverable = computed(
  () =>
    isStale.value &&
    detail.value?.recoverable === true &&
    detail.value?.pending_confirmation !== true,
)

const connectionLabel = computed(() => {
  if (isStale.value) return null
  if (!isLiveRun.value) return null
  return live.value.connected ? '事件流已连接' : '连接中…'
})

const isPreparationRun = computed(() => run.value?.status === 'draft')

function preparationStageFor(event) {
  if (!event) {
    return {
      title: live.value.connected
        ? '已连接研究直播，正在启动后台准备'
        : '正在连接研究直播，准备研究范围',
      detail:
        '任务已经提交成功。系统会先理解研究委托，再按需检索公开资料；这个阶段不需要额外配置或操作。',
    }
  }

  if (event.event === 'call_attempt_started') {
    if (event.provider_kind === 'tavily' || event.node === 'discovery') {
      return {
        title: '正在检索公开资料，发现并核验候选竞品',
        detail: '系统正在为候选对象收集公开来源佐证，完成后会自动切换到候选确认。',
      }
    }
    if (event.node === 'prepare_scope') {
      return {
        title: '正在理解研究委托，生成范围建议',
        detail: '模型正在把研究问题整理成可确认的研究范围，随后会继续处理候选发现。',
      }
    }
  }

  if (event.event === 'call_attempt_succeeded') {
    if (event.provider_kind === 'tavily' || event.node === 'discovery') {
      return {
        title: '候选资料已检索，正在整理候选结果',
        detail: '公开来源已返回，系统正在核对候选对象并准备确认清单。',
      }
    }
    if (event.node === 'prepare_scope') {
      return {
        title: '范围建议已生成，正在准备候选确认',
        detail: '研究委托已经完成初步规划，接下来会把候选对象和来源整理到确认页。',
      }
    }
  }

  if (event.event === 'model_retry_scheduled') {
    return {
      title: live.value.retryNotice?.switching ? '正在切换备用模型' : '正在重新连接模型',
      detail: '本次等待不会丢失任务；执行记录会继续同步，完成后页面会自动更新。',
    }
  }

  if (event.event === 'graph_execution_started') {
    return {
      title: '正在启动研究范围准备',
      detail: '后台执行已经开始，系统会实时同步模型调用和公开检索进度。',
    }
  }

  if (event.event === 'run_created') {
    return {
      title: '任务已提交，正在启动后台准备',
      detail: '系统正在建立研究范围。你可以留在本页查看事件流，完成后会自动进入候选确认。',
    }
  }

  if (event.event === 'graph_invoked') {
    return {
      title: '研究范围准备已完成，正在同步确认信息',
      detail: '后台准备已经收尾，页面会在确认载荷同步后自动切换。',
    }
  }

  return {
    title: '正在后台准备研究范围',
    detail: '系统会实时同步模型调用与公开检索进度，完成后自动切换到候选确认。',
  }
}

const preparationEvent = computed(() => {
  if (!isPreparationRun.value) return null
  return (
    live.value.events.find((event) =>
      [
        'run_created',
        'graph_execution_started',
        'call_attempt_started',
        'call_attempt_succeeded',
        'model_retry_scheduled',
        'graph_invoked',
      ].includes(event.event),
    ) || null
  )
})

const preparationStage = computed(() =>
  isPreparationRun.value ? preparationStageFor(preparationEvent.value) : null,
)

const preparationFeedEmpty = computed(() => {
  if (!isLiveRun.value) return '正在读取后台准备进度…'
  if (!live.value.connected) return '正在连接事件流，后台研究准备将继续执行…'
  return '事件流已连接，正在等待后台准备进度…'
})

const modelIdleLabel = computed(() =>
  isPreparationRun.value ? '候选准备阶段暂未发起下一次模型调用' : '尚未发起模型调用',
)
</script>

<template>
  <div class="page">
    <template v-if="run">
      <!-- 卷宗头 -->
      <header class="dossier-head rise-in">
        <div class="dossier-meta">
          <span class="overline overline--accent">研究卷宗</span>
          <button class="dossier-id mono" :title="run.run_id" @click="copyId">
            {{ shortId(run.run_id) }}…
            <span class="copy-hint">{{ copied ? '已复制' : '复制完整 ID' }}</span>
          </button>
          <span class="dossier-time">更新于 {{ fmtDateTime(run.updated_at) }}</span>
          <span class="dossier-rev mono">范围 rev.{{ run.scope_revision }}</span>
        </div>
        <div class="dossier-title-row">
          <h1 class="dossier-title">{{ run.query || '（未记录研究问题）' }}</h1>
          <StatusStamp :status="run.status" size="lg" />
        </div>
        <div class="dossier-entities">
          <span v-for="name in run.entities || []" :key="name" class="entity-chip">{{ name }}</span>
        </div>
      </header>

      <!-- ============ 候选选择确认点 ============ -->
      <section v-if="viewMode === 'candidates'" class="confirm-zone rise-in" style="animation-delay: 0.08s">
        <div class="confirm-banner">
          <div class="confirm-banner-icon" aria-hidden="true">?</div>
          <div>
            <b>系统已核验 {{ candidates.length }} 个候选竞品，等待你的决定</b>
            <span>候选池不按搜索网页数量截断，当前汇集自 {{ candidateSourceCount }} 个公开来源。勾选加入研究范围前请核对类别、来源佐证和最终名称；未勾选的候选视为拒绝。</span>
          </div>
        </div>

        <div v-for="group in candidateGroups" :key="group.label" class="cand-group">
          <div class="cand-group-head">
            <span>{{ group.label }}</span>
            <small>{{ group.items.length }} 个候选</small>
          </div>
          <div class="cand-grid">
            <div
              v-for="c in group.items"
              :key="c.candidate_id"
              class="cand-card sheet"
              :class="{ 'cand-card--on': picks[c.candidate_id]?.selected }"
              role="checkbox"
              :aria-checked="picks[c.candidate_id]?.selected === true"
              :aria-label="`将 ${c.entity.name} 纳入研究范围`"
              tabindex="0"
              @click="picks[c.candidate_id].selected = !picks[c.candidate_id].selected"
              @keydown.enter.prevent="picks[c.candidate_id].selected = !picks[c.candidate_id].selected"
              @keydown.space.prevent="picks[c.candidate_id].selected = !picks[c.candidate_id].selected"
            >
              <div class="cand-top">
                <span class="cand-check" aria-hidden="true">
                  <svg v-if="picks[c.candidate_id]?.selected" viewBox="0 0 12 12"><path d="M2.5 6.5 5 9l4.5-5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </span>
                <span class="cand-name">{{ c.entity.name }}</span>
              </div>
              <p v-if="c.summary" class="cand-summary">{{ c.summary }}</p>
              <p v-if="c.source_excerpt" class="cand-excerpt">{{ c.source_excerpt }}</p>
              <a class="cand-source" :href="c.source_url" target="_blank" rel="noopener" @click.stop>
                <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 9.5 13 3M9 3h4v4H3V3h4" /></svg>
                {{ c.source_title }}
              </a>
              <div v-if="picks[c.candidate_id]?.selected" class="cand-rename" @click.stop>
                <label class="cand-rename-label">确认最终对象名称</label>
                <input
                  v-model="picks[c.candidate_id].name"
                  class="input cand-rename-input"
                  :class="{ 'cand-rename-input--empty': !picks[c.candidate_id].name.trim() }"
                  placeholder="必须确认名称"
                />
              </div>
            </div>
          </div>
        </div>

        <div class="confirm-actions">
          <button class="btn btn--ghost" :disabled="actionBusy" @click="rejectAllCandidates">全部拒绝，仅用已有对象</button>
          <button class="btn btn--primary" :disabled="!namesValid || selectedCount === 0 || actionBusy" @click="submitCandidates">
            {{ actionBusy ? '提交中…' : `添加所选 ${selectedCount} 个候选并继续` }}
          </button>
        </div>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>
      </section>

      <!-- ============ 范围确认确认点 ============ -->
      <section v-else-if="viewMode === 'scope'" class="confirm-zone rise-in" style="animation-delay: 0.08s">
        <div class="confirm-banner">
          <div class="confirm-banner-icon" aria-hidden="true">!</div>
          <div>
            <b>请审阅并冻结研究范围</b>
            <span>范围冻结后开始消耗研究预算，网页内容不能再改变它。维度与排除项可在下方微调。</span>
          </div>
        </div>

        <div v-if="pendingScope" class="scope-sheet pressed">
          <div class="scope-row">
            <div class="scope-key">研究对象</div>
            <div class="scope-val">
              <span v-for="e in pendingScope.entities" :key="e.entity_id" class="entity-chip" :class="{ 'entity-chip--discovered': !e.user_provided }">
                {{ e.name }}
                <i v-if="!e.user_provided" title="来自候选发现">发现</i>
              </span>
            </div>
          </div>
          <div class="scope-row">
            <div class="scope-key">分析层级</div>
            <div class="scope-val scope-levels">
              {{ pendingScope.levels.map((lv) => LEVEL_LABELS[lv]).join(' · ') }}
            </div>
          </div>
          <div v-for="lv in pendingScope.levels" :key="lv" class="scope-row">
            <div class="scope-key scope-key--dim">{{ LEVEL_LABELS[lv] }}维度</div>
            <div class="scope-val">
              <TagInput v-model="pendingScope.dimensions[lv]" placeholder="添加维度" />
            </div>
          </div>
          <div class="scope-row">
            <div class="scope-key">排除项</div>
            <div class="scope-val">
              <TagInput v-model="pendingScope.exclusions" placeholder="明确不研究的内容" />
            </div>
          </div>
          <div class="scope-row scope-row--foot">
            <div class="scope-key">范围版本</div>
            <div class="scope-val mono">rev.{{ pendingScope.revision }}（确认后冻结）</div>
          </div>
        </div>

        <div class="confirm-actions">
          <button class="btn btn--ghost" :disabled="actionBusy" @click="confirmScope(false)">取消任务</button>
          <button class="btn btn--primary" :disabled="actionBusy" @click="confirmScope(true)">
            {{ actionBusy ? '正在提交…' : '确认范围并冻结，开始研究' }}
            <svg v-if="!actionBusy" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
          </button>
        </div>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>
      </section>

      <!-- 只读状态显示等待确认，但 Checkpoint 投影尚未带回可操作载荷时禁止猜测提交 -->
      <section
        v-else-if="viewMode === 'confirmation_unavailable'"
        class="confirmation-missing sheet rise-in"
        style="animation-delay: 0.08s"
      >
        <span class="overline overline--accent">确认信息待同步</span>
        <h2>当前没有可安全提交的确认载荷</h2>
        <p>
          系统记录显示任务正在等待确认，但尚未取得对应的候选或范围快照。页面不会使用空范围或猜测版本提交，请重新执行一次只读核对。
        </p>
        <button class="btn btn--ghost" :disabled="actionBusy" @click="reloadDetail">
          {{ actionBusy ? '正在核对…' : '重新读取任务状态' }}
        </button>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>
      </section>

      <!-- ============ 研究直播 ============ -->
      <template v-else-if="viewMode === 'live'">
        <div v-if="isStale" class="stale-note rise-in">
          <div class="stale-copy">
            <b>{{ isRecoverable ? '该任务已中断，可从本地检查点恢复。' : '该任务当前未在本进程执行。' }}</b>
            <span v-if="isRecoverable">
              恢复会继续真实研究并产生预算消耗，只有点击下方按钮才会执行；刷新、查询和事件订阅都不会自动恢复。
            </span>
            <span v-else>当前检查点不可由 Web 安全恢复，请核对任务状态或查看错误信息。</span>
          </div>
          <div v-if="isRecoverable" class="stale-actions">
            <button class="btn btn--primary" :disabled="actionBusy" @click="resumeResearch">
              {{ actionBusy ? '正在恢复…' : '恢复研究' }}
            </button>
            <code class="mono" title="高级用户命令行入口">uv run opencompete resume {{ run.run_id }}</code>
          </div>
        </div>
        <div v-if="actionError" class="action-error action-error--live">{{ actionError }}</div>
        <div v-if="!isStale" class="live-actions rise-in">
          <button
            class="btn btn--stop"
            :class="{ 'btn--stop-armed': cancelArmed }"
            :disabled="actionBusy"
            @click="cancelArmed ? stopResearch() : armCancel()"
          >
            {{ cancelArmed ? '确认停止？再点一次执行' : '停止研究' }}
          </button>
          <span v-if="cancelArmed" class="stop-hint">停止是终态：已产生的调用照常计费，任务不可恢复</span>
        </div>
        <div v-if="preparationStage" class="draft-note rise-in">
          <div class="stale-copy">
            <b>{{ preparationStage.title }}</b>
            <span>{{ preparationStage.detail }}</span>
            <small>候选确认准备完成后，本页会自动切换；你也可以先离开，执行记录会持续落盘。</small>
          </div>
        </div>
        <section class="live-track sheet rise-in" style="animation-delay: 0.06s">
          <div class="live-track-head">
            <span class="overline overline--accent">研究链路</span>
            <span
              v-if="connectionLabel"
              class="conn-badge mono"
              :class="{ 'conn-badge--on': live.connected }"
            >
              <i class="conn-dot"></i>{{ connectionLabel }}
            </span>
          </div>
          <PipelineTrack
            :track-index="isLiveRun ? live.trackIndex : 0"
            :finished="isLiveRun ? live.finished : false"
            :units="isLiveRun ? live.units : []"
            :unit-states="isLiveRun ? live.unitStates : {}"
          />
        </section>

        <div class="live-grid">
          <section class="feed-panel sheet rise-in" style="animation-delay: 0.12s">
            <div class="panel-head">
              <span class="overline">执行台账 · events.jsonl</span>
              <span class="feed-count mono">{{ isLiveRun ? live.events.length : 0 }} 条</span>
            </div>
            <div class="feed-scroll">
              <EventFeed
                :events="isLiveRun ? live.events : []"
                :empty-label="preparationFeedEmpty"
              />
            </div>
          </section>

          <aside class="live-side">
            <div class="sheet panel rise-in" style="animation-delay: 0.16s">
              <div class="panel-head"><span class="overline">预算水位</span></div>
              <div class="meters">
                <BudgetMeter label="输入 tokens" :spent="live.spent.input_tokens" :reserved="live.reserved.input_tokens" :limit="live.limit.input_tokens" />
                <BudgetMeter label="输出 tokens" :spent="live.spent.output_tokens" :reserved="live.reserved.output_tokens" :limit="live.limit.output_tokens" />
                <BudgetMeter label="Tavily 检索" :spent="live.spent.tavily_calls" :reserved="live.reserved.tavily_calls" :limit="live.limit.tavily_calls" kind="calls" />
                <BudgetMeter label="成本 (USD)" :spent="live.spent.cost_usd" :reserved="live.reserved.cost_usd" :limit="live.limit.cost_usd" kind="usd" />
              </div>
            </div>

            <div class="sheet panel rise-in" style="animation-delay: 0.2s">
              <div class="panel-head"><span class="overline">模型运行时</span></div>
              <div v-if="live.currentModel" class="model-card">
                <div class="model-row">
                  <span class="model-provider">{{ live.currentModel.provider }}</span>
                  <span class="model-role" :class="{ 'model-role--fallback': live.currentModel.fallback }">
                    {{ live.currentModel.fallback ? '备用' : '主' }}
                  </span>
                </div>
                <div class="model-name mono">{{ live.currentModel.model }}</div>
              </div>
              <div v-else class="model-idle">{{ modelIdleLabel }}</div>

              <div v-if="live.retryNotice" class="retry-note">
                <b>
                  {{ live.retryNotice.switching ? '正在切换备用模型' : '正在重新连接' }}
                  {{ live.retryNotice.nextAttempt }}/{{ live.retryNotice.total }}
                  （{{ live.retryNotice.delay ? `${live.retryNotice.delay} 秒后` : '立即' }}）
                </b>
                <span>{{ live.retryNotice.detail }}</span>
              </div>
            </div>
          </aside>
        </div>
      </template>

      <!-- ============ 完成态 ============ -->
      <section v-else-if="viewMode === 'done'" class="done-zone rise-in" style="animation-delay: 0.08s">
        <div class="done-grid">
          <div class="done-main pressed">
            <div class="done-check" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5 9.5 18 20 6.5" /></svg>
            </div>
            <h2 class="done-title">{{ run.status === 'partial' ? '研究部分完成' : '研究已完成并发布' }}</h2>
            <p class="done-desc">
              {{ run.status === 'partial'
                ? run.partial_reason || '预算耗尽，已发布现有成果'
                : '报告与结构化证据链已通过硬门禁校验，原子发布至本地卷宗。' }}
            </p>
            <div class="done-stats" v-if="bundle">
              <div class="done-stat"><b class="mono">{{ bundle.claims.length }}</b><span>结论</span></div>
              <div class="done-stat"><b class="mono">{{ bundle.evidence.length }}</b><span>证据</span></div>
              <div class="done-stat"><b class="mono">{{ bundle.sources.length }}</b><span>来源</span></div>
              <div class="done-stat"><b class="mono">{{ bundle.comparisons.length }}</b><span>对比</span></div>
            </div>
            <RouterLink :to="`/runs/${run.run_id}/report`" class="btn btn--primary done-cta">
              阅读研究报告
              <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
            </RouterLink>
          </div>

          <div class="done-side">
            <div class="sheet panel">
              <div class="panel-head"><span class="overline">质量评测</span></div>
              <div v-if="run.race_overall" class="score-rows">
                <div class="score-row">
                  <span>RACE Overall</span>
                  <b class="mono score-good">{{ run.race_overall.toFixed(1) }}</b>
                </div>
                <div class="score-row" v-if="run.fact_citation_accuracy">
                  <span>FACT 引用可靠率</span>
                  <b class="mono score-good">{{ (run.fact_citation_accuracy * 100).toFixed(0) }}%</b>
                </div>
                <div class="score-row">
                  <span>确定性硬门禁</span>
                  <b class="score-good">通过</b>
                </div>
                <p class="score-note">RACE 评价用户报告；FACT 与硬门禁评价同一 run 的结构化 Bundle。</p>
              </div>
              <div v-else class="score-empty">该任务尚未运行离线评测。</div>
            </div>

            <div class="sheet panel" v-if="bundle">
              <div class="panel-head"><span class="overline">预算结算</span></div>
              <div class="meters">
                <BudgetMeter label="输入 tokens" :spent="bundle.budget.spent.input_tokens" :limit="bundle.budget.limit.input_tokens" />
                <BudgetMeter label="输出 tokens" :spent="bundle.budget.spent.output_tokens" :limit="bundle.budget.limit.output_tokens" />
                <BudgetMeter label="Tavily 检索" :spent="bundle.budget.spent.tavily_calls" :limit="bundle.budget.limit.tavily_calls" kind="calls" />
                <BudgetMeter label="成本 (USD)" :spent="bundle.budget.spent.cost_usd" :limit="bundle.budget.limit.cost_usd" kind="usd" />
              </div>
            </div>

            <div class="sheet panel" v-if="bundle && bundle.report.limitations.length">
              <div class="panel-head">
                <span class="overline">产物诊断</span>
                <span class="diag-count mono">{{ bundle.report.limitations.length }}</span>
              </div>
              <p class="diag-note">
                流水线对结构化产物的自检记录（非报告正文，原样保留；提取器提示为模型原文）。
              </p>
              <ul class="diag-list">
                <li v-for="l in bundle.report.limitations" :key="l">{{ l }}</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      <!-- ============ 失败 / 取消 ============ -->
      <section v-else-if="viewMode === 'failed'" class="fail-zone pressed rise-in" style="animation-delay: 0.08s">
        <div class="fail-head">
          <span class="fail-code mono">[{{ run.error?.code || 'unexpected_error' }}]</span>
          <h2>任务执行失败</h2>
        </div>
        <p class="fail-msg">{{ run.error?.message || '发生未知错误' }}</p>
        <div v-if="run.recoverable" class="fail-recovery">
          <p>检查点仍可继续。恢复会使用当前模型与连接设置，并继续消耗本任务剩余预算。</p>
          <button
            type="button"
            class="btn btn--primary"
            :disabled="actionBusy"
            @click="resumeResearch"
          >{{ actionBusy ? '正在恢复…' : '恢复研究' }}</button>
        </div>
        <div v-else class="fail-hint">
          当前检查点无法安全继续。可在命令行复核本地任务：
          <code class="mono">uv run opencompete resume {{ run.run_id }}</code>
        </div>
        <p v-if="actionError" class="settings-error" role="alert">{{ actionError }}</p>
      </section>

      <section v-else-if="viewMode === 'cancelled'" class="cancel-zone sheet rise-in" style="animation-delay: 0.08s">
        <h2>任务已取消</h2>
        <p>该研究在范围确认阶段被取消，未消耗研究预算。</p>
        <RouterLink to="/new" class="btn btn--ink">重新发起研究</RouterLink>
      </section>
    </template>

    <div v-else-if="loading" class="missing sheet">
      <h2>正在读取卷宗…</h2>
    </div>

    <div v-else class="missing sheet">
      <h2>找不到该卷宗</h2>
      <p v-if="loadError" class="missing-err">{{ loadError }}</p>
      <RouterLink to="/runs" class="btn btn--ghost">返回历史卷宗</RouterLink>
    </div>
  </div>
</template>

<style scoped>
/* 卷宗头 */
.dossier-head {
  border-bottom: 3px double var(--line-ink);
  padding-bottom: 20px;
  margin-bottom: 26px;
}

.dossier-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.dossier-id {
  font-size: 12px;
  color: var(--ink-soft);
  background: var(--paper-dim);
  border: 1px solid var(--line-strong);
  border-radius: 3px;
  padding: 2px 8px;
  cursor: pointer;
  position: relative;
}

.dossier-id:hover {
  border-color: var(--ink);
}

.copy-hint {
  margin-left: 6px;
  font-size: 10px;
  color: var(--ink-ghost);
}

.dossier-time,
.dossier-rev {
  font-size: 12px;
  color: var(--ink-faint);
}

.dossier-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-top: 10px;
}

.dossier-title {
  font-family: var(--font-serif);
  font-size: 27px;
  font-weight: 900;
  line-height: 1.4;
  max-width: 760px;
}

.dossier-entities {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.entity-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  padding: 3px 11px;
  background: var(--paper-raised);
  border: 1.5px solid var(--ink);
  border-radius: 999px;
}

.entity-chip--discovered {
  border-style: dashed;
  border-color: var(--forest);
  color: var(--forest);
}

.entity-chip i {
  font-style: normal;
  font-size: 10px;
  background: var(--forest);
  color: var(--paper-raised);
  padding: 0 5px;
  border-radius: 6px;
}

/* 确认区 */
.confirm-banner {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  background: var(--amber-wash);
  border: 1.5px solid var(--amber);
  border-radius: var(--radius);
  padding: 15px 18px;
  margin-bottom: 22px;
}

.confirm-banner-icon {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  background: var(--amber);
  color: var(--paper-raised);
  border-radius: 50%;
  font-family: var(--font-serif);
  font-weight: 900;
  font-size: 17px;
}

.confirm-banner b {
  display: block;
  font-size: 14.5px;
}

.confirm-banner span {
  display: block;
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-top: 3px;
  line-height: 1.7;
}

.cand-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}

.cand-group + .cand-group {
  margin-top: 24px;
}

.cand-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin: 0 2px 10px;
  color: var(--ink);
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 700;
}

.cand-group-head small {
  color: var(--ink-faint);
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 700;
}

.cand-card {
  padding: 16px 18px;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.12s ease, box-shadow 0.15s ease;
}

.cand-card:hover {
  transform: translateY(-2px);
}

.cand-card--on {
  border: 1.5px solid var(--forest);
  box-shadow: 3px 3px 0 rgba(46, 97, 68, 0.35);
}

.cand-top {
  display: flex;
  align-items: center;
  gap: 11px;
}

.cand-check {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  border: 2px solid var(--line-strong);
  border-radius: 3px;
  display: grid;
  place-items: center;
  color: var(--paper-raised);
  transition: all 0.15s ease;
}

.cand-card--on .cand-check {
  background: var(--forest);
  border-color: var(--forest);
}

.cand-check svg {
  width: 12px;
  height: 12px;
}

.cand-name {
  font-family: var(--font-serif);
  font-size: 16.5px;
  font-weight: 700;
}

.cand-summary {
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.7;
  margin: 9px 0 10px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.cand-excerpt {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-left: 2px solid var(--amber);
  background: var(--amber-wash);
  color: var(--ink-soft);
  font-size: 12px;
  line-height: 1.65;
}

.cand-source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--slate);
  text-decoration: none;
  border-bottom: 1px dotted var(--slate);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cand-source:hover {
  color: var(--vermilion);
  border-color: var(--vermilion);
}

.cand-rename {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--line-strong);
}

.cand-rename-label {
  display: block;
  font-size: 11.5px;
  font-weight: 700;
  color: var(--forest);
  margin-bottom: 6px;
}

.cand-rename-input--empty {
  border-color: var(--vermilion);
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 14px;
  margin-top: 22px;
}

/* 范围确认单 */
.scope-sheet {
  padding: 8px 26px;
}

.scope-row {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 18px;
  padding: 15px 0;
  border-bottom: 1px dotted var(--line-strong);
  align-items: baseline;
}

.scope-row:last-child {
  border-bottom: none;
}

.scope-key {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 14px;
  color: var(--ink);
}

.scope-key--dim {
  font-size: 13px;
  color: var(--ink-soft);
}

.scope-val {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.scope-val > div {
  width: 100%;
}

.scope-levels {
  font-size: 14px;
  font-weight: 500;
}

.scope-row--foot .scope-val {
  font-size: 12.5px;
  color: var(--ink-faint);
}

/* 直播区 */
.live-track {
  padding: 18px 24px 20px;
  margin-bottom: 20px;
}

.live-track-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

/* 直播连接状态徽标 */
.conn-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  padding: 3px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  color: var(--ink-faint);
}

.conn-badge--on {
  border-color: var(--forest);
  color: var(--forest);
}

.conn-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line-strong);
}

.conn-badge--on .conn-dot {
  background: var(--forest);
  animation: pulse-ring 1.8s infinite;
}

/* 孤儿中间态提示 */
.stale-note {
  margin-bottom: 16px;
  padding: 12px 18px;
  background: var(--amber-wash);
  border-left: 3px solid var(--amber);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.8;
}

/* 范围准备期（draft）提示：后台执行候选发现与范围规划 */
.draft-note {
  margin-bottom: 16px;
  padding: 12px 18px;
  background: var(--paper-dim);
  border-left: 3px solid var(--slate);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.8;
}

/* 直播视图操作区：停止研究（两段式确认） */
.live-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.btn--stop {
  border: 1px solid var(--vermilion);
  color: var(--vermilion);
  background: transparent;
}

.btn--stop:hover:not(:disabled) {
  background: var(--vermilion);
  color: var(--paper-raised);
}

.btn--stop-armed {
  background: var(--vermilion);
  color: var(--paper-raised);
  animation: pulse-ring 1.2s infinite;
}

.stop-hint {
  font-size: 12.5px;
  color: var(--vermilion);
}

.stale-copy b,
.stale-copy span {
  display: block;
}

.stale-copy span {
  margin-top: 3px;
}

.stale-copy small {
  display: block;
  margin-top: 5px;
  color: var(--ink-faint);
  font-size: 11.5px;
}

.stale-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 13px;
  flex-wrap: wrap;
}

.stale-note code {
  background: var(--paper-raised);
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 1px 8px;
  font-size: 12px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.confirmation-missing {
  padding: 28px 32px;
}

.confirmation-missing h2 {
  margin: 8px 0;
  font-family: var(--font-serif);
  font-size: 21px;
}

.confirmation-missing p {
  max-width: 720px;
  margin-bottom: 18px;
  color: var(--ink-soft);
  font-size: 13.5px;
  line-height: 1.8;
}

/* 确认动作错误 */
.action-error {
  margin-top: 12px;
  padding: 10px 16px;
  background: var(--vermilion-wash);
  border-left: 3px solid var(--vermilion);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--vermilion-deep);
}

.action-error--live {
  margin: 0 0 16px;
}

.missing-err {
  font-size: 13px;
  color: var(--ink-faint);
  margin: 10px 0 16px;
}

.live-grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 20px;
  align-items: start;
}

.feed-panel {
  overflow: hidden;
}

.panel {
  padding: 16px 18px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-bottom: 11px;
  border-bottom: 2px solid var(--ink);
  margin-bottom: 4px;
}

.feed-panel .panel-head {
  padding: 16px 18px 11px;
  margin-bottom: 0;
}

.feed-count {
  font-size: 11px;
  color: var(--ink-ghost);
}

.feed-scroll {
  max-height: 520px;
  overflow-y: auto;
}

.live-side {
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: sticky;
  top: 20px;
}

.meters {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding-top: 10px;
}

.model-card {
  padding-top: 10px;
}

.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.model-provider {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 15px;
}

.model-role {
  font-size: 10.5px;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 8px;
  background: var(--forest-wash);
  color: var(--forest);
  border: 1px solid currentColor;
}

.model-role--fallback {
  background: var(--amber-wash);
  color: var(--amber);
}

.model-name {
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-top: 3px;
}

.model-idle {
  padding-top: 10px;
  font-size: 12.5px;
  color: var(--ink-ghost);
}

.retry-note {
  margin-top: 12px;
  background: var(--amber-wash);
  border-left: 3px solid var(--amber);
  border-radius: var(--radius);
  padding: 10px 13px;
}

.retry-note b {
  display: block;
  font-size: 12.5px;
  color: var(--amber);
}

.retry-note span {
  display: block;
  font-size: 11.5px;
  color: var(--ink-soft);
  margin-top: 3px;
  word-break: break-all;
}

/* 完成态 */
.done-grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 20px;
  align-items: start;
}

.done-main {
  padding: 34px 38px;
}

.done-check {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--forest);
  color: var(--paper-raised);
  display: grid;
  place-items: center;
  margin-bottom: 16px;
}

.done-check svg {
  width: 26px;
  height: 26px;
}

.done-title {
  font-family: var(--font-serif);
  font-size: 24px;
  font-weight: 900;
}

.done-desc {
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 8px;
  line-height: 1.75;
  max-width: 480px;
}

.done-stats {
  display: flex;
  gap: 34px;
  margin: 22px 0 26px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 15px 0;
}

.done-stat b {
  display: block;
  font-size: 25px;
  font-weight: 600;
}

.done-stat span {
  font-size: 12px;
  color: var(--ink-faint);
}

.done-side {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.score-rows {
  padding-top: 10px;
}

.score-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 13.5px;
  padding: 7px 0;
  border-bottom: 1px dotted var(--line-strong);
}

.score-row b {
  font-size: 16px;
  font-weight: 600;
}

.score-good {
  color: var(--forest);
}

.score-note {
  font-size: 11.5px;
  color: var(--ink-faint);
  line-height: 1.7;
  margin-top: 10px;
}

.score-empty {
  padding: 14px 0 6px;
  font-size: 12.5px;
  color: var(--ink-ghost);
}

/* 产物诊断 */
.diag-count {
  font-size: 10.5px;
  color: var(--ink-ghost);
}

.diag-note {
  font-size: 11.5px;
  color: var(--ink-faint);
  line-height: 1.7;
  margin: 10px 0 12px;
}

.diag-list {
  list-style: none;
  max-height: 260px;
  overflow-y: auto;
}

.diag-list li {
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-soft);
  padding: 6px 0 6px 16px;
  position: relative;
  border-bottom: 1px dotted var(--line);
  word-break: break-word;
}

.diag-list li:last-child {
  border-bottom: none;
}

.diag-list li::before {
  content: '△';
  position: absolute;
  left: 0;
  top: 8px;
  font-size: 9px;
  color: var(--amber);
}

/* 失败 / 取消 */
.fail-zone {
  padding: 30px 36px;
  border-color: var(--vermilion);
  box-shadow: 4px 4px 0 var(--vermilion);
}

.fail-head {
  display: flex;
  align-items: baseline;
  gap: 14px;
}

.fail-code {
  font-size: 12px;
  color: var(--vermilion);
  font-weight: 600;
}

.fail-head h2 {
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 900;
}

.fail-msg {
  margin-top: 10px;
  font-size: 14.5px;
  color: var(--ink-soft);
}

.fail-recovery {
  display: grid;
  gap: 12px;
  margin-top: 18px;
  padding: 14px;
  border: 1px solid var(--line-strong);
  background: var(--paper-raised);
}

.fail-recovery p {
  margin: 0;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.6;
}

.fail-hint {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  color: var(--ink-faint);
}

.fail-hint code {
  display: inline-block;
  margin-left: 8px;
  background: var(--ink);
  color: var(--paper);
  padding: 3px 10px;
  border-radius: 3px;
  font-size: 12px;
}

.cancel-zone {
  padding: 34px 38px;
  text-align: center;
}

.cancel-zone h2 {
  font-family: var(--font-serif);
  font-size: 21px;
}

.cancel-zone p {
  color: var(--ink-faint);
  margin: 10px 0 20px;
}

.missing {
  padding: 60px;
  text-align: center;
}

.missing h2 {
  font-family: var(--font-serif);
  margin-bottom: 18px;
}

@media (max-width: 1080px) {
  .live-grid,
  .done-grid {
    grid-template-columns: 1fr;
  }
  .cand-grid {
    grid-template-columns: 1fr;
  }
  .live-side {
    position: static;
  }
}
</style>
