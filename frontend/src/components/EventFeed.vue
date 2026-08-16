<script setup>
import { NODE_LABELS, fmtTokens } from '../utils/format.js'

defineProps({
  events: { type: Array, default: () => [] },
})

function timeOf(e) {
  const d = new Date(e.occurred_at)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const STATUS_TEXT = {
  awaiting_confirmation: '等待用户确认',
  scope_frozen: '研究范围已冻结',
  researching: '并行研究执行中',
  synthesizing: '正在撰写最终报告',
  completed: '已完成',
  partial: '部分完成',
  failed: '执行失败',
  cancelled: '已取消',
}

function tagOf(e) {
  if (e.event === 'model_retry_scheduled') return { text: '重试', cls: 'retry' }
  if (e.event === 'call_attempt_failed') return { text: '失败', cls: 'fail' }
  if (e.event === 'run_failed' || e.event === 'budget_exhausted') return { text: '失败', cls: 'fail' }
  if (e.event === 'run_published') return { text: '发布', cls: 'ok' }
  if (e.provider_kind === 'tavily') return { text: '搜索', cls: 'search' }
  if (e.provider_kind === 'llm') return { text: '模型', cls: 'llm' }
  return { text: '系统', cls: 'sys' }
}

function describe(e) {
  const node = NODE_LABELS[e.node] || e.node
  switch (e.event) {
    case 'run_created':
      return '任务已创建，草拟研究范围'
    case 'graph_execution_started':
      return e.mode === 'resume' ? '从确认点恢复，继续执行研究流程' : '开始执行研究流程'
    case 'graph_invoked':
    case 'graph_resumed':
      return '本轮执行结束'
    case 'awaiting_confirmation':
      return '已到达确认点，等待用户决定'
    case 'run_status_changed':
      return STATUS_TEXT[e.status] || `任务状态更新：${e.status}`
    case 'run_published':
      return '报告与结构化产物已通过硬门禁，原子发布'
    case 'run_failed':
      return `任务失败：${e.error || e.error_code || '未知错误'}`
    case 'budget_exhausted':
      return '研究预算耗尽，以部分完成发布已有成果'
    case 'call_attempt_started':
      if (e.provider_kind === 'tavily') {
        return `${node} · 发起 Tavily 检索${e.query ? `「${e.query}」` : ''}`
      }
      return `${node} · 调用 ${e.provider}/${e.model}${e.fallback ? '（备用）' : ''}`
    case 'call_attempt_succeeded':
      if (e.provider_kind === 'tavily') {
        return `${node} · 检索完成，消耗 ${e.tavily_calls} 次额度`
      }
      return `${node} · 响应完成，输出 ${fmtTokens(e.output_tokens)} tokens`
    case 'call_attempt_failed':
      return `${node} · 调用失败：${e.error_message || e.error_type || '未知错误'}`
    case 'call_attempt_cancelled_before_send':
      return `${node} · 调用在发送前被取消，未消耗额度`
    case 'model_retry_scheduled': {
      const action = e.next_fallback && !e.fallback ? '切换备用模型' : '重新连接'
      const wait = e.delay_seconds ? `${e.delay_seconds} 秒后` : '立即'
      return `${action} ${e.next_attempt}/${e.total_attempts}（${wait}）→ ${e.next_provider}/${e.next_model}`
    }
    default:
      return e.event
  }
}
</script>

<template>
  <div class="feed">
    <TransitionGroup name="feed">
      <div
        v-for="e in events"
        :key="e.seq"
        class="row"
        :class="{
          'row--fail': e.event === 'call_attempt_failed',
          'row--retry': e.event === 'model_retry_scheduled',
          'row--ok': e.event === 'run_published' || e.event === 'gate_passed',
        }"
      >
        <span class="row-time mono">{{ timeOf(e) }}</span>
        <span class="row-tag" :class="`tag--${tagOf(e).cls}`">{{ tagOf(e).text }}</span>
        <span class="row-text">
          {{ describe(e) }}
          <span v-if="e.duration_ms" class="row-dur mono">{{ (e.duration_ms / 1000).toFixed(1) }}s</span>
        </span>
      </div>
    </TransitionGroup>
    <div v-if="!events.length" class="feed-empty">等待事件流…</div>
  </div>
</template>

<style scoped>
.feed {
  display: flex;
  flex-direction: column;
}

.row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--line);
  font-size: 12.5px;
  line-height: 1.55;
}

.row--fail {
  background: var(--vermilion-wash);
  border-left: 3px solid var(--vermilion);
}

.row--retry {
  background: var(--amber-wash);
  border-left: 3px solid var(--amber);
}

.row--ok {
  background: var(--forest-wash);
  border-left: 3px solid var(--forest);
}

.row-time {
  font-size: 11px;
  color: var(--ink-faint);
  flex-shrink: 0;
}

.row-tag {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.1em;
  padding: 1px 6px;
  border-radius: 2px;
  border: 1px solid currentColor;
}

.tag--llm { color: var(--slate); }
.tag--search { color: var(--forest); }
.tag--sys { color: var(--ink-faint); }
.tag--retry { color: var(--amber); }
.tag--fail { color: var(--vermilion); }
.tag--ok { color: var(--forest); }

.row-text {
  color: var(--ink-soft);
  min-width: 0;
}

.row-dur {
  color: var(--ink-ghost);
  font-size: 11px;
  margin-left: 6px;
}

.feed-empty {
  padding: 28px;
  text-align: center;
  color: var(--ink-ghost);
  font-size: 13px;
}

.feed-enter-active {
  transition: all 0.35s cubic-bezier(0.22, 0.8, 0.3, 1);
}

.feed-enter-from {
  opacity: 0;
  transform: translateY(-10px);
  background: var(--amber-wash);
}
</style>
