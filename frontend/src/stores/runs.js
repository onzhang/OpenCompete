// 前端状态层：对接本地 opencompete-api 服务。
// 列表 / 详情 / Bundle 经 HTTP 拉取；研究直播经 SSE 订阅 events.jsonl 权威流。
import { defineStore } from 'pinia'
import { api, eventsUrl } from '../api/client.js'
import { NODE_TO_TRACK, PUBLISHED_TRACK_INDEX } from '../constants/pipeline.js'

// LLM 调用在 started 阶段尚不知实际输出量，按保守估算预留输出额度，
// settle 时释放同一笔预留、再按实际值结转（与后端 reserve/settle 语义对齐）。
const OUTPUT_RESERVE_ESTIMATE = 2400

// EventSource 实例不进 Pinia state（避免响应式代理包装原生对象）
let eventSource = null
let pollTimer = null

function emptyUsage() {
  return { input_tokens: 0, output_tokens: 0, tavily_calls: 0, cost_usd: 0 }
}

function addUsage(target, e, sign = 1) {
  target.input_tokens += sign * (e.input_tokens || 0)
  target.output_tokens += sign * (e.output_tokens || 0)
  target.tavily_calls += sign * (e.tavily_calls || 0)
  target.cost_usd += sign * (e.cost_usd || 0)
}

// 消除浮点残差并防止负值（释放预留后可能出现 -1e-17 这类噪声）
function normalizeUsage(u) {
  u.input_tokens = Math.max(0, Math.round(u.input_tokens))
  u.output_tokens = Math.max(0, Math.round(u.output_tokens))
  u.tavily_calls = Math.max(0, Math.round(u.tavily_calls))
  u.cost_usd = Math.max(0, Number(u.cost_usd.toFixed(4)))
}

function displayModelName(value) {
  if (typeof value !== 'string') return value
  const separator = value.indexOf(':')
  return separator > 0 ? value.slice(separator + 1) : value
}

function modelProviderLabel(event) {
  if (event.connection_format === 'openai_chat_completions') return 'OpenAI Chat Completions'
  if (event.connection_format === 'anthropic_messages') return 'Anthropic Messages'
  const model = typeof event.model === 'string' ? event.model : ''
  if (model.startsWith('openai:')) return 'OpenAI Chat Completions'
  if (model.startsWith('anthropic:')) return 'Anthropic Messages'
  return event.provider || '模型'
}

// 一次调用的预留登记键：同一节点 + 同一研究单元 + 同一供应商类型
function holdKey(e) {
  return `${e.node || '-'}|${e.unit_id || '-'}|${e.provider_kind || '-'}`
}

function freshLive() {
  return {
    runId: null,
    connected: false,
    lastSeq: 0,
    errorStreak: 0,
    events: [],
    trackIndex: 0,
    failedAt: -1,
    units: [],
    unitStates: {},
    currentModel: null,
    retryNotice: null,
    spent: emptyUsage(),
    reserved: emptyUsage(),
    holds: {},
    limit: emptyUsage(),
    finished: false,
  }
}

function confirmationRevision(detail, kind) {
  if (
    detail?.pending_confirmation !== true ||
    detail.confirmation_kind !== kind ||
    !Number.isInteger(detail.expected_revision) ||
    detail.expected_revision < 0
  ) {
    throw new Error('确认快照已失效或尚未同步；请先重新读取任务状态后再提交。')
  }
  return detail.expected_revision
}

export const useRunsStore = defineStore('runs', {
  state: () => ({
    runs: [],
    runsLoading: false,
    runsError: null,
    budgetLimit: null,
    detailByRun: {},
    bundlesByRun: {},
    live: freshLive(),
  }),

  getters: {
    runById: (state) => (id) => state.runs.find((r) => r.run_id === id) || null,
    detailFor: (state) => (id) => state.detailByRun[id] || null,
    bundleFor: (state) => (id) => state.bundlesByRun[id] || null,
    activeRuns: (state) =>
      state.runs.filter((r) =>
        ['draft', 'awaiting_confirmation', 'scope_frozen', 'researching', 'synthesizing'].includes(
          r.status,
        ),
      ),
    publishedRuns: (state) =>
      state.runs.filter((r) => ['completed', 'partial'].includes(r.status)),
  },

  actions: {
    /* ---------- 读取 ---------- */
    async fetchRuns() {
      this.runsLoading = true
      this.runsError = null
      try {
        this.runs = await api.listRuns()
      } catch (err) {
        this.runsError = err.message
      } finally {
        this.runsLoading = false
      }
    },

    async fetchConfig() {
      if (this.budgetLimit) return this.budgetLimit
      try {
        const cfg = await api.config()
        this.budgetLimit = cfg.budget_limit
      } catch {
        this.budgetLimit = null
      }
      return this.budgetLimit
    },

    async fetchRunDetail(runId) {
      const detail = await api.getRun(runId)
      this._projectDetail(detail)
      return detail
    },

    async fetchBundle(runId) {
      if (this.bundlesByRun[runId]) return this.bundlesByRun[runId]
      const bundle = await api.getBundle(runId)
      this.bundlesByRun[runId] = bundle
      return bundle
    },

    /* ---------- 新建研究（POST /runs） ---------- */
    async createRun(form) {
      const detail = await api.createRun({
        query: form.query,
        entities: form.entities,
        focus_points: form.focusPoints,
        exclusions: form.exclusions,
        allowed_urls: form.allowedUrls,
        discover_candidates: form.discover,
      })
      this._projectDetail(detail)
      return detail.run_id
    },

    /* ---------- 候选选择确认点 ---------- */
    async submitCandidateSelection(runId, choices) {
      const detail = this.detailByRun[runId]
      const updated = await api.confirmCandidates(runId, {
        expected_revision: confirmationRevision(detail, 'candidate_selection'),
        selected_candidates: choices.map((c) => ({
          candidate_id: c.candidate_id,
          entity_name: c.entity_name,
        })),
      })
      this._projectDetail(updated)
      // 选择提交后任务进入后台范围规划，同样接续事件直播。
      this.maybeStartLive(updated)
      return updated
    },

    /* ---------- 范围确认确认点 ---------- */
    async submitScopeConfirmation(runId, accepted, editedScope = null) {
      const detail = this.detailByRun[runId]
      const body = {
        accepted,
        expected_revision: confirmationRevision(detail, 'scope_confirmation'),
      }
      if (accepted && editedScope) {
        // 显式序列化编辑后的范围（维度 / 排除项可能被用户改过）
        body.entities = editedScope.entities.map((e) => ({
          name: e.name,
          entity_id: e.entity_id || '',
          aliases: e.aliases || [],
          description: e.description ?? null,
          user_provided: e.user_provided !== false,
        }))
        body.levels = editedScope.levels.slice()
        body.dimensions = Object.fromEntries(
          editedScope.levels.map((lv) => [lv, (editedScope.dimensions[lv] || []).slice()]),
        )
        body.exclusions = (editedScope.exclusions || []).slice()
      }
      const resp = await api.confirmScope(runId, body)
      // 确认接口返回完整详情投影。先在同一同步阶段更新详情与列表，
      // 再连接事件流；不能用可能滞后的 GET/列表结果覆盖 202 投影。
      this._projectDetail(resp)
      if (accepted) this.maybeStartLive(resp)
      return resp
    },

    /* ---------- 显式恢复中断研究 ---------- */
    async resumeRun(runId) {
      const detail = await api.resumeRun(runId)
      this._projectDetail(detail)
      this.startLive(runId, { after: 0 })
      return detail
    },

    /* ---------- 停止运行中的研究（终态，不保留恢复） ---------- */
    async cancelRun(runId) {
      const detail = await api.cancelRun(runId)
      this.stopLive()
      this._projectDetail(detail)
      await this.fetchRuns().catch(() => {})
      return detail
    },

    /* ---------- 彻底删除任务（不可恢复） ---------- */
    async deleteRun(runId) {
      await api.deleteRun(runId)
      this.stopLive()
      delete this.detailByRun[runId]
      delete this.bundlesByRun[runId]
      this.runs = this.runs.filter((r) => r.run_id !== runId)
    },

    /* ---------- 详情 / 列表原子投影 ---------- */
    _projectDetail(detail) {
      if (!detail?.run_id) return
      const runId = detail.run_id
      this.detailByRun[runId] = detail
      const row = listFields(detail)
      const idx = this.runs.findIndex((item) => item.run_id === runId)
      if (idx >= 0) this.runs[idx] = { ...this.runs[idx], ...row }
      else this.runs.unshift(row)
    },

    _patchProjection(runId, patch) {
      const current = this.detailByRun[runId]
      if (current) this.detailByRun[runId] = { ...current, ...patch }
      const idx = this.runs.findIndex((item) => item.run_id === runId)
      if (idx >= 0) this.runs[idx] = { ...this.runs[idx], ...listPatchFields(patch) }
    },

    /* ---------- 事件直播（SSE） ---------- */
    // active 且无待确认的执行态任务（含 draft 期的候选发现与范围规划）
    // 都应订阅事件流；awaiting_confirmation 是 settled 状态，SSE 补发
    // end 帧收尾后由 _finishLive 刷新详情并切到确认界面。
    maybeStartLive(detail) {
      if (
        detail?.active &&
        !detail.pending_confirmation &&
        ['draft', 'researching', 'synthesizing', 'scope_frozen'].includes(detail.status)
      ) {
        this.startLive(detail.run_id, { after: 0 })
      }
    },

    startLive(runId, { after = null } = {}) {
      this.stopLive()
      const resume = after ?? this.live.lastSeq
      if (this.live.runId !== runId) {
        this.live = freshLive()
      }
      this.live.runId = runId
      this.live.finished = false
      const limit = this.detailByRun[runId]?.budget?.limit || this.budgetLimit
      if (limit) this.live.limit = { ...limit }

      eventSource = new EventSource(eventsUrl(runId, resume))
      eventSource.onopen = () => {
        this.live.connected = true
        this.live.errorStreak = 0
      }
      eventSource.onmessage = (ev) => {
        const seq = Number(ev.lastEventId)
        if (Number.isFinite(seq) && seq > 0) this.live.lastSeq = seq
        let payload = null
        try {
          payload = JSON.parse(ev.data)
        } catch {
          return
        }
        this._applyEvent(payload)
      }
      eventSource.addEventListener('end', () => {
        this._finishLive()
      })
      eventSource.onerror = () => {
        this.live.connected = false
        this.live.errorStreak += 1
        // 浏览器会自动重连（带 Last-Event-ID）；连续失败则降级轮询
        if (this.live.errorStreak > 3) {
          this.stopLive()
          this._pollFallback(runId)
        }
      }
    },

    stopLive() {
      if (eventSource) {
        eventSource.close()
        eventSource = null
      }
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      this.live.connected = false
    },

    _pollFallback(runId) {
      pollTimer = setInterval(async () => {
        try {
          const detail = await this.fetchRunDetail(runId)
          if (
            ['completed', 'partial', 'failed', 'cancelled', 'awaiting_confirmation'].includes(
              detail.status,
            ) ||
            (detail.stale && detail.recoverable)
          ) {
            clearInterval(pollTimer)
            pollTimer = null
            this._finishLive()
          }
        } catch {
          /* 下一轮再试 */
        }
      }, 10000)
    },

    /* ---------- SSE 事件 reducer ---------- */
    _applyEvent(raw) {
      const live = this.live
      // seq 取 SSE 帧 id（events.jsonl 行号），供列表 key 与断线续传使用
      const e = { ...raw, seq: live.lastSeq || live.events.length + 1 }
      live.events.unshift(e)
      if (live.events.length > 200) live.events.pop()

      // 主链路推进（validate 无独立事件，由 run_published 驱动到终点）
      const trackIdx = NODE_TO_TRACK[e.node]
      if (trackIdx != null && trackIdx > live.trackIndex) live.trackIndex = trackIdx
      if (e.event === 'run_published') {
        live.trackIndex = PUBLISHED_TRACK_INDEX
      }

      // 并行研究单元：从事件流动态发现注册
      if (e.unit_id && (e.node === 'researcher' || e.node === 'compress_research')) {
        const summary = typeof e.unit_summary === 'string' ? e.unit_summary.trim() : ''
        if (!(e.unit_id in live.unitStates)) {
          live.units.push({
            unit_id: e.unit_id,
            topic: summary || `研究单元 ${live.units.length + 1}`,
          })
          live.unitStates[e.unit_id] = 'pending'
        } else if (summary) {
          const unit = live.units.find((item) => item.unit_id === e.unit_id)
          if (unit && unit.topic.startsWith('研究单元 ')) unit.topic = summary
        }
        if (e.event === 'call_attempt_started') {
          live.unitStates[e.unit_id] =
            e.node === 'compress_research' ? 'compressing' : 'searching'
        }
        if (e.event === 'call_attempt_failed') live.unitStates[e.unit_id] = 'retrying'
        if (e.event === 'call_attempt_succeeded' && e.node === 'compress_research') {
          live.unitStates[e.unit_id] = 'done'
        }
      }

      // 预算：started 登记预留，settle（成功或失败）释放同一笔并结转实际用量。
      // 失败调用同样计入 spent —— 已发出的请求不会因失败而退费。
      if (e.event === 'call_attempt_started') {
        const hold = {
          input_tokens: e.input_tokens || 0,
          output_tokens: e.provider_kind === 'llm' ? OUTPUT_RESERVE_ESTIMATE : 0,
          tavily_calls: e.tavily_calls || 0,
          cost_usd: e.cost_usd || 0,
        }
        live.holds[holdKey(e)] = hold
        addUsage(live.reserved, hold)
        normalizeUsage(live.reserved)
      } else if (
        e.event === 'call_attempt_succeeded' ||
        e.event === 'call_attempt_failed' ||
        e.event === 'call_attempt_cancelled_before_send'
      ) {
        const key = holdKey(e)
        const hold = live.holds[key]
        if (hold) {
          addUsage(live.reserved, hold, -1)
          delete live.holds[key]
        }
        normalizeUsage(live.reserved)
        if (e.event !== 'call_attempt_cancelled_before_send') {
          addUsage(live.spent, e)
          normalizeUsage(live.spent)
        }
      }

      // 当前模型与重试提示
      if (e.provider_kind === 'llm' && e.event === 'call_attempt_started') {
        live.currentModel = {
          provider: modelProviderLabel(e),
          model: displayModelName(e.model),
          connectionRole: e.connection_role || (e.fallback ? 'fallback' : 'primary'),
          fallback: !!e.fallback,
        }
        live.retryNotice = null
      }
      if (e.event === 'model_retry_scheduled') {
        live.retryNotice = {
          nextAttempt: e.next_attempt,
          total: e.total_attempts,
          delay: e.delay_seconds,
          switching: !!e.next_fallback && !e.fallback,
          nextProvider: e.next_provider,
          nextModel: e.next_model,
          detail: e.error_message,
        }
      }

      // 失败 / 预算耗尽
      if (e.event === 'run_failed' || e.event === 'budget_exhausted') {
        live.failedAt = live.trackIndex
      }

      // run 状态同时投影到详情和列表。详情页优先读取 detailByRun，
      // 若只改列表会继续停留在旧确认表单。
      const statusEvent =
        e.event === 'run_status_changed' ||
        e.event === 'run_published' ||
        e.event === 'run_failed' ||
        e.event === 'run_cancelled' ||
        e.event === 'budget_exhausted' ||
        e.event === 'awaiting_confirmation'
      const status = statusEvent ? eventStatus(e) : null
      const patch = {
        budget_spent_usd: Number(live.spent.cost_usd.toFixed(2)),
      }
      if (status) {
        const terminal = ['completed', 'partial', 'failed', 'cancelled'].includes(status)
        patch.status = status
        patch.updated_at = e.occurred_at || this.detailByRun[live.runId]?.updated_at
        patch.active = !terminal && status !== 'awaiting_confirmation'
        if (status !== 'awaiting_confirmation') {
          patch.pending_confirmation = false
          patch.confirmation_kind = null
          patch.pending_scope = null
          patch.candidates = []
          patch.expected_revision = null
          patch.stale = false
          patch.recoverable = false
        }
      }
      this._patchProjection(live.runId, patch)
      // awaiting_confirmation 事件先于 end 帧到达；立即拉取完整确认快照
      // （候选列表 / 范围提案），让页面直接切到确认界面而不是中间态。
      if (status === 'awaiting_confirmation') {
        this.fetchRunDetail(live.runId).catch(() => {})
      }
    },

    async _finishLive() {
      const live = this.live
      live.finished = true
      live.holds = {}
      live.reserved = emptyUsage()
      this.stopLive()
      const runId = live.runId
      if (!runId) return
      // 以服务端为准刷新终态，并预取 Bundle
      try {
        const detail = await this.fetchRunDetail(runId)
        if (['completed', 'partial'].includes(detail.status)) {
          await this.fetchBundle(runId).catch(() => {})
        }
      } catch {
        /* 详情暂不可用时保持现状 */
      }
      await this.fetchRuns()
    },
  },
})

// 从详情响应提取列表行所需字段
function listFields(detail) {
  return {
    run_id: detail.run_id,
    status: detail.status,
    scope_revision: detail.scope_revision,
    artifact_revision: detail.artifact_revision,
    updated_at: detail.updated_at,
    query: detail.query,
    entities: detail.entities || [],
    confirmation_kind: detail.confirmation_kind,
    budget_spent_usd: detail.budget_spent_usd,
    budget_limit_usd: detail.budget_limit_usd,
    partial_reason: detail.partial_reason,
    error: detail.error,
    race_overall: detail.race_overall,
    fact_citation_accuracy: detail.fact_citation_accuracy,
    active: detail.active,
  }
}

function eventStatus(event) {
  if (event.status) return event.status
  if (event.event === 'run_published') return 'completed'
  if (event.event === 'run_failed') return 'failed'
  if (event.event === 'run_cancelled') return 'cancelled'
  if (event.event === 'budget_exhausted') return 'partial'
  if (event.event === 'awaiting_confirmation') return 'awaiting_confirmation'
  return null
}

function listPatchFields(patch) {
  return Object.fromEntries(
    ['status', 'updated_at', 'budget_spent_usd', 'active', 'confirmation_kind']
      .filter((key) => Object.hasOwn(patch, key))
      .map((key) => [key, patch[key]]),
  )
}
