<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { useRunsStore } from '../stores/runs.js'
import { useSettingsStore } from '../stores/settings.js'

const settingsStore = useSettingsStore()
const runsStore = useRunsStore()
const form = reactive({})
const saveError = ref('')
const saveNotice = ref('')
const activeSection = ref('models')
const credentials = reactive({})
const credentialBusy = ref('')
const credentialError = ref('')
const testState = reactive({})
const clearTarget = ref(null)

const sections = [
  { id: 'models', label: '模型路由', groups: ['模型路由', '模型输出'] },
  { id: 'budget', label: '预算与成本', groups: ['任务预算与成本'] },
  { id: 'execution', label: '研究执行', groups: ['研究执行'] },
  { id: 'transport', label: '传输与搜索', groups: ['传输与搜索'] },
]

const view = computed(() => settingsStore.view)
const fields = computed(() => view.value?.fields || {})
const activeRuns = computed(() => runsStore.activeRuns)
const credentialChangeBlocked = computed(() => activeRuns.value.length > 0)
const dirty = computed(() => {
  if (!view.value) return false
  return Object.entries(fields.value).some(([name, item]) => !sameValue(form[name], item.value))
})

const modelFields = [
  { name: 'research_model', label: '研究模型', target: 'research' },
  { name: 'summarization_model', label: '摘要模型', target: 'summarization' },
  { name: 'compression_model', label: '压缩模型', target: 'compression' },
  { name: 'final_report_model', label: '最终报告模型', target: 'final_report' },
  { name: 'fallback_model', label: '备用模型', target: 'fallback' },
  { name: 'judge_model', label: '评测 Judge', target: 'judge' },
]

const FIELD_LABELS = {
  primary_provider_name: '主供应商显示名称',
  fallback_provider_name: '备用供应商显示名称',
  primary_model_attempts: '主模型尝试次数',
  fallback_model_attempts: '备用模型尝试次数',
  model_retry_delays_seconds: '重试等待时间（秒）',
  primary_disable_thinking: '关闭主模型 Thinking',
  fallback_disable_thinking: '关闭备用模型 Thinking',
  research_model_max_tokens: '研究输出 Token 上限',
  summarization_model_max_tokens: '摘要输出 Token 上限',
  compression_model_max_tokens: '压缩输出 Token 上限',
  final_report_model_max_tokens: '最终报告输出 Token 上限',
  artifact_extraction_model_max_tokens: '结构化提取输出 Token 上限',
  final_report_input_max_chars: '报告输入字符上限',
  judge_model_max_tokens: 'Judge 输出 Token 上限',
  max_input_tokens: '输入 Token 总预算',
  max_output_tokens: '输出 Token 总预算',
  max_tavily_calls: 'Tavily 调用预算',
  max_cost_usd: '成本上限（USD）',
  llm_input_cost_per_million_usd: '输入费率（每百万 Token / USD）',
  llm_output_cost_per_million_usd: '输出费率（每百万 Token / USD）',
  tavily_cost_per_call_usd: 'Tavily 单次费率（USD）',
  price_config_version: '本地成本版本标识',
  max_concurrent_research_units: '并行研究单元数',
  max_concurrent_summarizations: '并行摘要数',
  max_researcher_iterations: 'Researcher 迭代上限',
  max_supervisor_model_turns: 'Supervisor 模型轮次上限',
  max_react_tool_calls: 'ReAct 工具调用上限',
  max_research_tool_calls: '研究工具调用上限',
  max_structured_output_retries: '结构化输出重试上限',
  llm_request_timeout_seconds: '模型请求超时（秒）',
  llm_sdk_max_retries: '模型 SDK 内部重试次数',
  long_form_streaming: '长文本流式聚合',
  max_content_length: '网页内容字符上限',
  tavily_max_results_per_query: '单次 Tavily 结果数',
  discovery_search_results_per_query: '候选发现单次结果数',
  discovery_binding_max_input_chars: '候选绑定输入字符上限',
  tavily_max_queries_per_call: '单次 Tavily Query 上限',
  tavily_extract_excerpt_max_chars: 'Tavily 摘录字符上限',
}

const credentialCards = [
  { name: 'OPENAI_API_KEY', label: 'OpenAI 兼容 API Key' },
  { name: 'OPENAI_BASE_URL', label: 'OpenAI 兼容 Base URL' },
  { name: 'ANTHROPIC_API_KEY', label: 'Anthropic 兼容 API Key' },
  { name: 'ANTHROPIC_BASE_URL', label: 'Anthropic 兼容 Base URL' },
  { name: 'GOOGLE_API_KEY', label: 'Google API Key' },
  { name: 'TAVILY_API_KEY', label: 'Tavily 搜索 API Key', target: 'tavily' },
]

onMounted(async () => {
  await Promise.all([settingsStore.fetch(), runsStore.fetchRuns()])
  hydrate()
})

watch(view, hydrate)

function hydrate() {
  if (!view.value) return
  for (const [name, item] of Object.entries(view.value.fields)) form[name] = cloneValue(item.value)
}

function cloneValue(value) {
  return Array.isArray(value) ? [...value] : value
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function labelFor(name) {
  return FIELD_LABELS[name] || name
}

function typeFor(name, value) {
  if (typeof value === 'boolean') return 'checkbox'
  if (typeof value === 'number') return 'number'
  return 'text'
}

function stepFor(name) {
  return name.includes('cost') || name.includes('seconds') ? '0.01' : '1'
}

function normalizeValue(name, value) {
  const initial = fields.value[name]?.value
  if (typeof initial === 'boolean') return Boolean(value)
  if (typeof initial === 'number') return Number(value)
  if (Array.isArray(initial)) {
    return String(value)
      .split(',')
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item))
  }
  return value === '' && (name === 'fallback_model' || name === 'judge_model') ? null : value
}

async function save() {
  if (!dirty.value || settingsStore.saving) return
  saveError.value = ''
  saveNotice.value = ''
  const values = {}
  for (const name of Object.keys(fields.value)) {
    const next = normalizeValue(name, form[name])
    if (!sameValue(next, fields.value[name].value)) values[name] = next
  }
  try {
    await settingsStore.save(values)
    hydrate()
    saveNotice.value = `已保存 rev.${settingsStore.view.revision}，仅影响后续新建任务。`
  } catch (err) {
    saveError.value = err.message
  }
}

function credentialStatus(name) {
  return view.value?.credentials.find((item) => item.name === name)
}

async function saveCredential(card) {
  const value = credentials[card.name]?.trim()
  if (!value || credentialBusy.value || credentialChangeBlocked.value) return
  credentialBusy.value = card.name
  credentialError.value = ''
  try {
    await settingsStore.changeCredential({ name: card.name, operation: 'set', value })
    credentials[card.name] = ''
  } catch (err) {
    credentialError.value = err.message
  } finally {
    credentialBusy.value = ''
  }
}

async function clearCredential() {
  if (!clearTarget.value) return
  credentialBusy.value = clearTarget.value.name
  credentialError.value = ''
  try {
    await settingsStore.changeCredential({ name: clearTarget.value.name, operation: 'clear' })
    clearTarget.value = null
  } catch (err) {
    credentialError.value = err.message
  } finally {
    credentialBusy.value = ''
  }
}

async function testConnection(target) {
  testState[target] = { pending: true, message: '' }
  try {
    const result = await settingsStore.testConnection(target)
    testState[target] = { pending: false, message: result.status === 'ok' ? '连接已验证' : '测试未完成', ok: result.status === 'ok' }
  } catch (err) {
    testState[target] = { pending: false, message: err.message, ok: false }
  }
}
</script>

<template>
  <div class="page settings-page">
    <header class="page-head rise-in settings-head">
      <div>
        <span class="overline overline--accent">LOCAL RUNTIME SETTINGS</span>
        <h1 class="page-title">运行<em>设置</em></h1>
      </div>
      <div v-if="view" class="revision-stamp mono">配置 rev.{{ view.revision }}</div>
    </header>

    <p class="settings-intro">模型、预算和研究执行参数会在新建任务时冻结；已有任务继续按创建时的非秘密设置运行。</p>

    <div v-if="settingsStore.loading && !view" class="loading-sheet sheet">正在读取本地设置…</div>
    <p v-else-if="settingsStore.error && !view" class="settings-error" role="alert">{{ settingsStore.error }}</p>

    <div v-else-if="view" class="settings-layout">
      <aside class="settings-nav sheet">
        <button
          v-for="section in sections"
          :key="section.id"
          type="button"
          class="settings-nav-item"
          :class="{ 'settings-nav-item--active': activeSection === section.id }"
          @click="activeSection = section.id"
        >
          {{ section.label }}
        </button>
        <span class="settings-nav-rule"></span>
        <button type="button" class="settings-nav-item" :class="{ 'settings-nav-item--active': activeSection === 'credentials' }" @click="activeSection = 'credentials'">连接凭据</button>
      </aside>

      <form class="settings-sheet sheet" @submit.prevent="save">
        <section v-show="activeSection === 'models'" class="settings-section">
          <div class="rule-heading"><span class="overline">MODEL ROUTING</span></div>
          <h2>模型路由与输出容量</h2>
          <p>所有模型沿用 <span class="mono">provider:model</span> 标识。备用模型只在临时 Provider 失败后接续同一个逻辑调用。</p>
          <div class="model-grid">
            <label v-for="item in modelFields" :key="item.name" class="field">
              <span class="field-label">{{ item.label }}</span>
              <div class="model-input-row">
                <input v-model="form[item.name]" class="input mono" type="text" :placeholder="item.name === 'fallback_model' || item.name === 'judge_model' ? '留空则关闭' : 'provider:model'" />
                <button type="button" class="icon-action" :title="`测试${item.label}连接（会产生少量调用成本）`" :aria-label="`测试${item.label}连接`" :disabled="testState[item.target]?.pending || !form[item.name]" @click="testConnection(item.target)">↗</button>
              </div>
              <span class="field-hint">{{ fields[item.name]?.source }} <span v-if="testState[item.target]?.message" class="test-result" :class="{ 'test-result--ok': testState[item.target]?.ok }">· {{ testState[item.target].message }}</span></span>
            </label>
          </div>
          <div class="field-grid route-controls">
            <label v-for="name in ['primary_provider_name', 'fallback_provider_name', 'primary_model_attempts', 'fallback_model_attempts', 'model_retry_delays_seconds', 'primary_disable_thinking', 'fallback_disable_thinking']" :key="name" class="field">
              <span class="field-label">{{ labelFor(name) }}</span>
              <input v-if="typeof fields[name].value === 'boolean'" v-model="form[name]" class="switch-input" type="checkbox" />
              <input v-else-if="Array.isArray(fields[name].value)" :value="form[name]?.join(', ')" class="input mono" type="text" @input="form[name] = $event.target.value" />
              <input v-else-if="typeof fields[name].value === 'number'" v-model.number="form[name]" class="input" type="number" min="1" step="1" />
              <input v-else v-model="form[name]" class="input" type="text" />
            </label>
          </div>
          <div class="field-grid">
            <label v-for="name in ['research_model_max_tokens', 'summarization_model_max_tokens', 'compression_model_max_tokens', 'final_report_model_max_tokens', 'artifact_extraction_model_max_tokens', 'judge_model_max_tokens']" :key="name" class="field">
              <span class="field-label">{{ labelFor(name) }}</span>
              <input v-model.number="form[name]" class="input" type="number" min="1" step="1" />
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'budget'" class="settings-section">
          <div class="rule-heading"><span class="overline">BUDGET LEDGER</span></div>
          <h2>任务预算与成本口径</h2>
          <p>预算在外部调用前预留，达到任一硬上限后任务停止新增调用并保留已完成结果。</p>
          <div class="field-grid">
            <label v-for="name in view.groups['任务预算与成本']" :key="name" class="field">
              <span class="field-label">{{ labelFor(name) }}</span>
              <input v-if="typeof fields[name].value === 'number'" v-model.number="form[name]" class="input" type="number" min="0" :step="stepFor(name)" />
              <input v-else v-model="form[name]" class="input mono" type="text" />
              <span class="field-hint">{{ fields[name].source }}</span>
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'execution'" class="settings-section">
          <div class="rule-heading"><span class="overline">RESEARCH CONTROL</span></div>
          <h2>研究执行控制</h2>
          <p>并行度、Supervisor 和 Researcher 的硬上限共同限制一次研究的工作量。</p>
          <div class="field-grid">
            <label v-for="name in view.groups['研究执行']" :key="name" class="field">
              <span class="field-label">{{ labelFor(name) }}</span>
              <input v-model.number="form[name]" class="input" type="number" min="1" step="1" />
              <span class="field-hint">{{ fields[name].source }}</span>
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'transport'" class="settings-section">
          <div class="rule-heading"><span class="overline">TRANSPORT AND SEARCH</span></div>
          <h2>传输与搜索边界</h2>
          <p>这些上限约束单次请求内容、模型传输和 Tavily 请求规模，不会修改已冻结范围。</p>
          <div class="field-grid">
            <label v-for="name in view.groups['传输与搜索']" :key="name" class="field">
              <span class="field-label">{{ labelFor(name) }}</span>
              <input v-if="typeof fields[name].value === 'boolean'" v-model="form[name]" class="switch-input" type="checkbox" />
              <input v-else-if="Array.isArray(fields[name].value)" :value="form[name]?.join(', ')" class="input mono" type="text" @input="form[name] = $event.target.value" />
              <input v-else v-model.number="form[name]" class="input" type="number" min="0" :step="stepFor(name)" />
              <span class="field-hint">{{ fields[name].source }}</span>
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'credentials'" class="settings-section">
          <div class="rule-heading"><span class="overline">CONNECTION CREDENTIALS</span></div>
          <h2>连接凭据</h2>
          <p>输入值只写入本机 <span class="mono">.env</span> 并同步到当前进程，页面不回显 Key 或 Base URL。测试连接会产生少量模型或搜索调用成本。</p>
          <p v-if="credentialChangeBlocked" class="settings-warning">存在 {{ activeRuns.length }} 个未终态任务。为保证可恢复性，完成或停止这些任务后才能替换凭据。</p>
          <p v-if="credentialError" class="settings-error" role="alert">{{ credentialError }}</p>
          <div class="credential-list">
            <article v-for="card in credentialCards" :key="card.name" class="credential-card pressed">
              <header>
                <div>
                  <h3>{{ card.label }}</h3>
                  <span class="mono">{{ card.name }}</span>
                </div>
                <span class="credential-state" :class="{ 'credential-state--ready': credentialStatus(card.name)?.configured }">{{ credentialStatus(card.name)?.configured ? '已配置' : '未配置' }}</span>
              </header>
              <div class="credential-meta">来源：{{ credentialStatus(card.name)?.source }}{{ credentialStatus(card.name)?.manageable ? '' : ' · 外部管理' }}</div>
              <div class="credential-input-row">
                <input v-model="credentials[card.name]" class="input mono" type="password" :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable" :placeholder="card.name.includes('BASE_URL') ? 'https://api.example.com/v1' : '输入新值以设置或替换'" autocomplete="off" />
                <button type="button" class="btn btn--ink" :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable || !credentials[card.name]?.trim() || credentialBusy === card.name" @click="saveCredential(card)">{{ credentialBusy === card.name ? '保存中…' : '保存' }}</button>
              </div>
              <footer>
                <button type="button" class="text-action" :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable || !credentialStatus(card.name)?.configured" @click="clearTarget = card">清除</button>
                <button v-if="card.target" type="button" class="text-action" :disabled="testState[card.target]?.pending" @click="testConnection(card.target)">{{ testState[card.target]?.pending ? '测试中…' : '测试连接' }}</button>
                <span v-if="card.target && testState[card.target]?.message" class="test-result" :class="{ 'test-result--ok': testState[card.target]?.ok }">{{ testState[card.target].message }}</span>
              </footer>
            </article>
          </div>
        </section>

        <footer v-if="activeSection !== 'credentials'" class="save-bar">
          <p v-if="saveError" class="settings-error" role="alert">{{ saveError }}</p>
          <p v-else-if="saveNotice" class="save-notice">{{ saveNotice }}</p>
          <span v-else>修改后的设置仅在新建研究任务时生效。</span>
          <button class="btn btn--primary" type="submit" :disabled="!dirty || settingsStore.saving">{{ settingsStore.saving ? '保存中…' : '保存设置' }}</button>
        </footer>
      </form>

      <aside class="settings-aside">
        <section class="settings-note sheet">
          <span class="overline">CURRENT POLICY</span>
          <h2>任务快照</h2>
          <p>任务创建后，模型、预算、并发与搜索限制会固定在本地 Manifest 中。恢复旧任务不会读取后来保存的设置。</p>
        </section>
        <section class="settings-note sheet">
          <span class="overline">SECRET BOUNDARY</span>
          <h2>零回显</h2>
          <p>API Key、Base URL 和 Authorization 信息不会进入页面响应、研究状态、Checkpoint、事件日志或业务产物。</p>
        </section>
      </aside>
    </div>

    <ConfirmDialog
      :open="Boolean(clearTarget)"
      title="清除本地凭据"
      :subject="clearTarget?.label || ''"
      :details="['将从本机 .env 与当前进程环境移除该值。', '已创建任务仍需保留原有凭据，完成或停止后才可执行。']"
      confirm-label="确认清除"
      pending-label="清除中…"
      :pending="Boolean(clearTarget && credentialBusy === clearTarget.name)"
      :error="credentialError"
      @cancel="clearTarget = null"
      @confirm="clearCredential"
    />
  </div>
</template>

<style scoped>
.settings-page { max-width: 1500px; }
.settings-head { display: flex; justify-content: space-between; align-items: end; gap: 18px; }
.settings-intro { max-width: 760px; margin: -8px 0 28px; color: var(--ink-soft); }
.revision-stamp { padding: 7px 10px; border: 1px solid var(--line-strong); color: var(--ink-soft); white-space: nowrap; }
.settings-layout { display: grid; grid-template-columns: 168px minmax(0, 1fr) 230px; gap: 20px; align-items: start; }
.settings-nav { position: sticky; top: 20px; display: grid; padding: 10px; border-radius: var(--radius-lg); }
.settings-nav-item { padding: 10px 11px; text-align: left; border: 0; border-left: 3px solid transparent; background: transparent; color: var(--ink-soft); font-weight: 700; }
.settings-nav-item:hover, .settings-nav-item--active { background: var(--paper-dim); color: var(--ink); border-left-color: var(--vermilion); }
.settings-nav-rule { height: 1px; margin: 9px 2px; background: var(--line); }
.settings-sheet { min-width: 0; padding: 28px; }
.settings-section h2, .settings-note h2 { margin: 12px 0 7px; font-family: var(--font-serif); font-size: 22px; line-height: 1.25; }
.settings-section > p { color: var(--ink-soft); font-size: 13.5px; margin-bottom: 22px; }
.field-grid, .model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 16px; }
.route-controls { margin: 22px 0; padding: 18px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.field { min-width: 0; }
.model-input-row { display: grid; grid-template-columns: minmax(0, 1fr) 38px; gap: 7px; }
.icon-action { display: grid; place-items: center; width: 38px; height: 40px; border: 1.5px solid var(--ink); border-radius: var(--radius); background: var(--ink); color: var(--paper); font-size: 18px; line-height: 1; }
.icon-action:hover:not(:disabled) { background: var(--vermilion); }
.icon-action:disabled { opacity: .45; cursor: not-allowed; }
.field-label { margin-bottom: 6px; font-size: 14px; overflow-wrap: anywhere; }
.switch-input { width: 38px; height: 22px; accent-color: var(--vermilion); }
.save-bar { position: sticky; bottom: 10px; display: flex; justify-content: space-between; align-items: center; gap: 16px; margin: 30px -4px -4px; padding: 13px 14px; border-top: 1px solid var(--line-strong); background: var(--paper-raised); color: var(--ink-soft); font-size: 13px; }
.save-notice { color: var(--forest); }
.settings-error, .settings-warning { padding: 10px 12px; border-left: 3px solid var(--vermilion); background: var(--vermilion-wash); color: var(--vermilion-deep); font-size: 13px; }
.settings-warning { margin: 0 0 16px; border-color: var(--amber); background: var(--amber-wash); color: var(--ink); }
.settings-aside { display: grid; gap: 16px; position: sticky; top: 20px; }
.settings-note { padding: 17px; }
.settings-note h2 { font-size: 18px; }
.settings-note p { color: var(--ink-soft); font-size: 12.5px; line-height: 1.65; }
.credential-list { display: grid; gap: 13px; }
.credential-card { padding: 16px; }
.credential-card header { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
.credential-card h3 { font-family: var(--font-serif); font-size: 16px; line-height: 1.35; }
.credential-card header .mono, .credential-meta { color: var(--ink-faint); font-size: 11px; overflow-wrap: anywhere; }
.credential-state { padding: 2px 7px; border: 1px solid var(--line-strong); color: var(--ink-faint); font-size: 11px; white-space: nowrap; }
.credential-state--ready { border-color: var(--forest); color: var(--forest); }
.credential-meta { margin-top: 5px; }
.credential-input-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 13px; }
.credential-input-row .btn { padding: 8px 13px; }
.credential-card footer { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 11px; }
.text-action { border: 0; background: transparent; color: var(--ink-soft); font-size: 12.5px; text-decoration: underline; text-underline-offset: 3px; }
.text-action:hover:not(:disabled) { color: var(--vermilion); }
.text-action:disabled { opacity: .45; cursor: not-allowed; }
.test-result { color: var(--vermilion-deep); font-size: 12px; overflow-wrap: anywhere; }
.test-result--ok { color: var(--forest); }
.loading-sheet { padding: 30px; color: var(--ink-soft); }
@media (max-width: 1180px) { .settings-layout { grid-template-columns: 156px minmax(0, 1fr); } .settings-aside { grid-column: 2; position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .settings-head { align-items: start; flex-direction: column; } .settings-layout { grid-template-columns: 1fr; } .settings-nav { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); } .settings-nav-rule { display: none; } .settings-sheet { padding: 20px 16px; } .field-grid, .model-grid { grid-template-columns: 1fr; } .settings-aside { grid-column: auto; grid-template-columns: 1fr; position: static; } .save-bar { align-items: stretch; flex-direction: column; } .credential-input-row { grid-template-columns: 1fr; } }
</style>
