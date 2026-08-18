<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { useRunsStore } from '../stores/runs.js'
import { useSettingsStore } from '../stores/settings.js'

const settingsStore = useSettingsStore()
const runsStore = useRunsStore()
const form = reactive({})
const credentials = reactive({})
const saveError = ref('')
const saveNotice = ref('')
const activeSection = ref('models')
const credentialBusy = ref('')
const credentialError = ref('')
const testState = reactive({})
const clearTarget = ref(null)
const revealedKeys = reactive({})
const connectionKeys = reactive({})
const newConnectionKeys = reactive({})
const connectionBaseUrls = reactive({})
const connectionErrors = reactive({})

const sections = [
  { id: 'models', label: '模型与连接' },
  { id: 'budget', label: '研究预算' },
  { id: 'execution', label: '研究深度与速度' },
  { id: 'search', label: '搜索范围' },
]

const modelFields = [
  { name: 'research_model', label: '研究模型' },
  { name: 'summarization_model', label: '摘要模型' },
  { name: 'compression_model', label: '压缩模型' },
  { name: 'final_report_model', label: '最终报告模型' },
]

const connections = [
  {
    role: 'primary',
    label: '主连接',
    formatField: 'primary_connection_format',
    modelField: 'research_model',
  },
  {
    role: 'fallback',
    label: '备用连接',
    formatField: 'fallback_connection_format',
    modelField: 'fallback_model',
  },
]

const FORMAT_OPTIONS = [
  { value: 'openai_chat_completions', label: 'OpenAI Chat Completions' },
  { value: 'anthropic_messages', label: 'Anthropic Messages' },
]

const credentialCards = [
  { name: 'TAVILY_API_KEY', label: 'Tavily 搜索 API Key', target: 'tavily' },
]

const FIELD_META = {
  research_model: {
    label: '研究模型',
    tip: '作用范围：后续新任务中的 Researcher。数值影响：模型能力越强，复杂检索与分析质量通常越高，响应时间和实际账单也可能增加。',
  },
  summarization_model: {
    label: '摘要模型',
    tip: '作用范围：后续新任务的网页资料摘要、范围规划和候选发现。数值影响：更强模型通常能提高长网页归纳质量，但会增加处理时间与实际账单。',
  },
  compression_model: {
    label: '压缩模型',
    tip: '作用范围：后续新任务的研究发现压缩。数值影响：更强模型更利于保留关键证据，响应时间和实际账单可能增加。',
  },
  final_report_model: {
    label: '最终报告模型',
    tip: '作用范围：后续新任务的最终可读报告。数值影响：更强模型通常改善叙事、比较与建议表达，响应时间和实际账单可能增加。',
  },
  fallback_model: {
    label: '备用模型',
    tip: '作用范围：后续新任务中主模型遇到限流、超时、连接错误或服务端错误后的接续调用。数值影响：留空时不降级；配置后可提高临时故障下的完成率。',
  },
  research_model_max_tokens: {
    label: '研究输出上限',
    tip: '作用范围：后续任务中每个 Researcher 的单次模型输出。数值影响：增大可容纳更完整的检索计划与结论，但单次响应更慢且实际账单可能增加。',
  },
  summarization_model_max_tokens: {
    label: '摘要输出上限',
    tip: '作用范围：后续任务中每个网页摘要和范围规划输出。数值影响：增大可保留更多原始资料细节，但会增加处理时间与实际账单。',
  },
  compression_model_max_tokens: {
    label: '压缩输出上限',
    tip: '作用范围：后续任务中研究发现的压缩阶段。数值影响：增大可保留更多跨来源发现，但会增加处理时间与实际账单。',
  },
  final_report_model_max_tokens: {
    label: '最终报告输出下限',
    tip: '作用范围：后续任务的最终报告生成。数值影响：增大可减少大型范围下的正文截断；系统仍会按冻结范围自动提高必要容量。',
  },
  max_input_tokens: {
    label: '输入 Token 总预算',
    tip: '作用范围：后续单个研究任务的全部模型输入。数值影响：达到上限后不再发起新调用；增大允许处理更多资料，也可能增加实际账单。',
  },
  max_output_tokens: {
    label: '输出 Token 总预算',
    tip: '作用范围：后续单个研究任务的全部模型输出。数值影响：达到上限后不再发起新调用；增大允许更深入研究与更长报告，也可能增加实际账单。',
  },
  max_tavily_calls: {
    label: 'Tavily 调用预算',
    tip: '作用范围：后续单个研究任务的 Tavily Search 与 Extract credit。数值影响：达到上限后停止新增检索；增大可覆盖更多来源并可能增加实际账单。',
  },
  max_cost_usd: {
    label: '总成本上限（USD）',
    tip: '作用范围：后续单个研究任务的本地保守预算门禁。数值影响：达到上限后停止新增外部调用；增大可延长研究，但不是供应商最终账单承诺。',
  },
  max_concurrent_research_units: {
    label: '并行研究单元数',
    tip: '作用范围：后续任务中可同时运行的 Researcher 数量。数值影响：增大通常缩短总耗时，但会提高瞬时并发、限流概率和预算消耗速度。',
  },
  max_researcher_iterations: {
    label: '研究迭代上限',
    tip: '作用范围：后续任务中 Supervisor 的补充研究轮次。数值影响：增大可继续追踪证据缺口，但会延长任务并可能增加外部调用。',
  },
  max_research_tool_calls: {
    label: '每单元研究工具额度',
    tip: '作用范围：后续任务中单个 Researcher 的搜索与资料工具调用。数值影响：增大可扩大单个专题的证据覆盖，但会增加耗时和 Tavily 消耗。',
  },
  tavily_max_results_per_query: {
    label: '单次 Tavily 结果数',
    tip: '作用范围：后续任务中 Researcher 的每条 Tavily 搜索。数值影响：增大可让每次检索看到更多来源，但会提高后续摘要工作量与调用消耗。',
  },
  discovery_search_results_per_query: {
    label: '候选发现单次结果数',
    tip: '作用范围：后续开启候选发现的任务。数值影响：增大可让每条发现检索覆盖更多来源；不会限制最终候选数量，但会增加处理量与调用消耗。',
  },
}

const CREDENTIAL_TIPS = {
  OPENAI_API_KEY: '作用范围：后续使用 OpenAI 兼容模型的新任务与主动连接测试。数值影响：替换后只改变认证身份，不改变研究预算或模型能力。',
  OPENAI_BASE_URL: '作用范围：后续 OpenAI 兼容模型的新任务与主动连接测试。数值影响：替换后改变请求的兼容服务端点，不改变研究预算或模型能力。',
  ANTHROPIC_API_KEY: '作用范围：后续使用 Anthropic 兼容模型的新任务与主动连接测试。数值影响：替换后只改变认证身份，不改变研究预算或模型能力。',
  ANTHROPIC_BASE_URL: '作用范围：后续 Anthropic 兼容模型的新任务与主动连接测试。数值影响：替换后改变请求的兼容服务端点，不改变研究预算或模型能力。',
  GOOGLE_API_KEY: '作用范围：后续使用 Google 模型的新任务与主动连接测试。数值影响：替换后只改变认证身份，不改变研究预算或模型能力。',
  TAVILY_API_KEY: '作用范围：后续研究任务的 Tavily 搜索和候选发现。数值影响：替换后只改变搜索服务认证身份，不改变每个任务的 Tavily 调用预算。',
}

const view = computed(() => settingsStore.view)
const fields = computed(() => view.value?.fields || {})
const activeRuns = computed(() => runsStore.activeRuns)
const credentialChangeBlocked = computed(() => activeRuns.value.length > 0)
const dirty = computed(() => {
  if (!view.value) return false
  return Object.entries(fields.value).some(([name, item]) => !sameValue(form[name], item.value))
})

onMounted(async () => {
  await Promise.all([settingsStore.fetch(), runsStore.fetchRuns()])
  hydrate()
})

watch(view, hydrate)

function hydrate() {
  if (!view.value) return
  for (const [name, item] of Object.entries(view.value.fields)) {
    form[name] = cloneValue(item.value)
  }
  for (const connection of connections) {
    connectionBaseUrls[connection.role] = connectionView(connection.role)?.base_url || ''
  }
}

function cloneValue(value) {
  return Array.isArray(value) ? [...value] : value
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function fieldMeta(name) {
  return FIELD_META[name] || { label: name, tip: '作用范围：后续新任务。数值影响：修改后会改变该任务的运行行为。' }
}

function normalizeValue(name, value) {
  const initial = fields.value[name]?.value
  if (typeof initial === 'number') return Number(value)
  return value === '' && name === 'fallback_model' ? null : value
}

function fieldNames(group) {
  return view.value?.groups[group] || []
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
    saveNotice.value = '已保存，修改仅影响后续新建任务。'
  } catch (err) {
    saveError.value = err.message
  }
}

function credentialName(role, kind) {
  const connection = connectionView(role)
  if (kind === 'key') return connection?.api_key_name
  return connection?.base_url_name
}

async function saveConnectionCredential(role, kind) {
  const value = kind === 'key' ? newConnectionKeys[role]?.trim() : connectionBaseUrls[role]?.trim()
  if (!value || credentialBusy.value || credentialChangeBlocked.value) return
  const name = credentialName(role, kind)
  credentialBusy.value = name
  credentialError.value = ''
  try {
    await settingsStore.changeCredential({ name, operation: 'set', value })
    if (kind === 'key') {
      connectionKeys[role] = ''
      newConnectionKeys[role] = ''
      revealedKeys[role] = false
    }
  } catch (err) {
    credentialError.value = err.message
  } finally {
    credentialBusy.value = ''
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

function connectionView(role) {
  return view.value?.connections?.find((item) => item.role === role)
}

async function revealKey(role) {
  if (revealedKeys[role]) {
    revealedKeys[role] = false
    connectionKeys[role] = ''
    return
  }
  connectionErrors[role] = ''
  try {
    const result = await settingsStore.revealConnectionKey(role)
    connectionKeys[role] = result.api_key || ''
    revealedKeys[role] = Boolean(result.api_key)
  } catch (err) {
    connectionErrors[role] = err.message
  }
}

async function testConnection(target) {
  testState[target] = { pending: true, message: '' }
  try {
    const result = await settingsStore.testConnection(target)
    testState[target] = {
      pending: false,
      message: result.status === 'ok' ? '连接已验证' : '测试未完成',
      ok: result.status === 'ok',
    }
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
    </header>

    <p class="settings-intro">新建任务使用当前设置；可恢复任务在你主动恢复时使用当前模型与连接。范围、预算和已完成进度仍属于原任务契约。</p>

    <div v-if="settingsStore.loading && !view" class="loading-sheet sheet">正在读取本地设置…</div>
    <p v-else-if="settingsStore.error && !view" class="settings-error" role="alert">{{ settingsStore.error }}</p>

    <div v-else-if="view" class="settings-layout">
      <aside class="settings-nav sheet" aria-label="设置分类">
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
      </aside>

      <form class="settings-sheet sheet" @submit.prevent="save">
        <section v-show="activeSection === 'models'" class="settings-section">
          <div class="rule-heading"><span class="overline">MODELS AND CONNECTIONS</span></div>
          <h2>模型与连接</h2>
          <p>主连接和备用连接分别配置兼容格式、公开 Base URL、API Key 与模型。可恢复任务在你主动点击恢复时使用当前连接设置；Thinking 由系统按模型路由处理。</p>

          <div class="connection-grid">
            <section v-for="connection in connections" :key="connection.role" class="connection-card">
              <div class="connection-card__head">
                <div>
                  <span class="overline">{{ connection.role === 'primary' ? 'PRIMARY ROUTE' : 'FALLBACK ROUTE' }}</span>
                  <h3>{{ connection.label }}</h3>
                </div>
                <span class="credential-state" :class="{ 'credential-state--ready': connectionView(connection.role)?.api_key_configured }">
                  {{ connectionView(connection.role)?.api_key_configured ? '已配置' : '未配置' }}
                </span>
              </div>
              <label class="field">
                <span class="field-label-row">兼容格式</span>
                <select v-model="form[connection.formatField]" class="input">
                  <option v-for="option in FORMAT_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field-label-row">Base URL</span>
                <input v-model="connectionBaseUrls[connection.role]" class="input mono" type="url" placeholder="https://api.example.com/v1" />
                <div class="credential-actions">
                  <span class="field-hint">{{ connectionView(connection.role)?.base_url_name }}</span>
                  <button type="button" class="text-action" :disabled="credentialChangeBlocked || !connectionBaseUrls[connection.role] || credentialBusy === connectionView(connection.role)?.base_url_name" @click="saveConnectionCredential(connection.role, 'base')">保存 Base URL</button>
                </div>
              </label>
              <label class="field">
                <span class="field-label-row">API Key</span>
                <div class="model-input-row">
                  <input
                    class="input mono"
                    :type="revealedKeys[connection.role] ? 'text' : 'password'"
                    :value="revealedKeys[connection.role] ? connectionKeys[connection.role] : '••••••••••••••••'"
                    readonly
                    autocomplete="off"
                  />
                  <button type="button" class="icon-action" :title="revealedKeys[connection.role] ? '隐藏当前 API Key' : '显示当前 API Key'" :aria-label="revealedKeys[connection.role] ? '隐藏当前 API Key' : '显示当前 API Key'" @click="revealKey(connection.role)">{{ revealedKeys[connection.role] ? '◉' : '◌' }}</button>
                </div>
                <p v-if="connectionErrors[connection.role]" class="settings-error" role="alert">{{ connectionErrors[connection.role] }}</p>
                <div class="credential-input-row">
                  <input v-model="newConnectionKeys[connection.role]" class="input mono" type="password" placeholder="输入新 Key 以替换" autocomplete="off" />
                  <button type="button" class="btn btn--ink" :disabled="credentialChangeBlocked || !newConnectionKeys[connection.role]?.trim() || credentialBusy === connectionView(connection.role)?.api_key_name" @click="saveConnectionCredential(connection.role, 'key')">{{ credentialBusy === connectionView(connection.role)?.api_key_name ? '保存中…' : '替换 Key' }}</button>
                </div>
              </label>
              <label class="field">
                <span class="field-label-row">模型名称</span>
                <input v-model="form[connection.modelField]" class="input mono" type="text" :placeholder="connection.role === 'fallback' ? '留空则不启用备用连接' : '例如 gpt-4.1'" />
              </label>
              <div class="connection-card__actions">
                <button type="button" class="btn btn--ink" :disabled="testState[connection.role]?.pending || (connection.role === 'fallback' && !form[connection.modelField])" @click="testConnection(connection.role)">
                  {{ testState[connection.role]?.pending ? '测试中…' : '测试连接' }}
                </button>
                <span v-if="testState[connection.role]?.message" class="test-result" :class="{ 'test-result--ok': testState[connection.role]?.ok }">{{ testState[connection.role].message }}</span>
              </div>
            </section>
          </div>

          <div class="settings-divider"></div>
          <h3 class="subheading">模型角色</h3>
          <div class="model-grid">
            <label v-for="item in modelFields" :key="item.name" class="field">
              <span class="field-label-row">
                <span>{{ item.label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${item.label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ fieldMeta(item.name).tip }}</span>
                </span>
              </span>
              <input v-model="form[item.name]" class="input mono" type="text" :placeholder="item.name === 'fallback_model' ? '留空则不启用' : '模型名称，不填写 provider 前缀'" />
              <span class="field-hint">{{ fields[item.name]?.source }}</span>
            </label>
          </div>

          <div class="settings-divider"></div>
          <h3 class="subheading">输出容量</h3>
          <div class="field-grid">
            <label v-for="name in fieldNames('模型与连接').filter((name) => name.endsWith('_max_tokens'))" :key="name" class="field">
              <span class="field-label-row">
                <span>{{ fieldMeta(name).label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${fieldMeta(name).label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ fieldMeta(name).tip }}</span>
                </span>
              </span>
              <input v-model.number="form[name]" class="input" type="number" min="1" step="1" />
              <span class="field-hint">{{ fields[name]?.source }}</span>
            </label>
          </div>

          <div class="settings-divider"></div>
          <h3 class="subheading">连接凭据</h3>
          <p class="section-note">输入值只写入本机 <span class="mono">.env</span>，页面不会回显 Key 或 Base URL。主动测试连接会产生少量模型或搜索调用成本。</p>
          <p v-if="credentialChangeBlocked" class="settings-warning">存在 {{ activeRuns.length }} 个未终态任务。为保证可恢复性，完成或停止这些任务后才能替换凭据。</p>
          <p v-if="credentialError" class="settings-error" role="alert">{{ credentialError }}</p>
          <div class="credential-list">
            <section v-for="card in credentialCards" :key="card.name" class="credential-row">
              <div class="credential-title">
                <span>{{ card.label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${card.label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ CREDENTIAL_TIPS[card.name] }}</span>
                </span>
                <span class="credential-state" :class="{ 'credential-state--ready': credentialStatus(card.name)?.configured }">{{ credentialStatus(card.name)?.configured ? '已配置' : '未配置' }}</span>
              </div>
              <div class="credential-meta"><span class="mono">{{ card.name }}</span> · 来源：{{ credentialStatus(card.name)?.source }}{{ credentialStatus(card.name)?.manageable ? '' : ' · 外部管理' }}</div>
              <div class="credential-input-row">
                <input
                  v-model="credentials[card.name]"
                  class="input mono"
                  type="password"
                  :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable"
                  :placeholder="card.name.includes('BASE_URL') ? 'https://api.example.com/v1' : '输入新值以设置或替换'"
                  autocomplete="off"
                />
                <button type="button" class="btn btn--ink" :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable || !credentials[card.name]?.trim() || credentialBusy === card.name" @click="saveCredential(card)">{{ credentialBusy === card.name ? '保存中…' : '保存' }}</button>
              </div>
              <div class="credential-actions">
                <button type="button" class="text-action" :disabled="credentialChangeBlocked || !credentialStatus(card.name)?.manageable || !credentialStatus(card.name)?.configured" @click="clearTarget = card">清除</button>
                <button v-if="card.target" type="button" class="text-action" :disabled="testState[card.target]?.pending" @click="testConnection(card.target)">{{ testState[card.target]?.pending ? '测试中…' : '测试连接' }}</button>
                <span v-if="card.target && testState[card.target]?.message" class="test-result" :class="{ 'test-result--ok': testState[card.target]?.ok }">{{ testState[card.target].message }}</span>
              </div>
            </section>
          </div>
        </section>

        <section v-show="activeSection === 'budget'" class="settings-section">
          <div class="rule-heading"><span class="overline">BUDGET GUARD</span></div>
          <h2>研究预算</h2>
          <p>预算会在外部调用前预留。达到任一硬上限后，任务停止新增调用并保留已经完成的结果。</p>
          <div class="field-grid">
            <label v-for="name in fieldNames('研究预算')" :key="name" class="field">
              <span class="field-label-row">
                <span>{{ fieldMeta(name).label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${fieldMeta(name).label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ fieldMeta(name).tip }}</span>
                </span>
              </span>
              <input v-model.number="form[name]" class="input" type="number" min="0" :step="name === 'max_cost_usd' ? '0.01' : '1'" />
              <span class="field-hint">{{ fields[name]?.source }}</span>
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'execution'" class="settings-section">
          <div class="rule-heading"><span class="overline">RESEARCH CONTROL</span></div>
          <h2>研究深度与速度</h2>
          <p>这些参数决定任务可同时展开多少专题，以及每个专题允许追踪证据到什么深度。</p>
          <div class="field-grid">
            <label v-for="name in fieldNames('研究深度与速度')" :key="name" class="field">
              <span class="field-label-row">
                <span>{{ fieldMeta(name).label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${fieldMeta(name).label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ fieldMeta(name).tip }}</span>
                </span>
              </span>
              <input v-model.number="form[name]" class="input" type="number" min="1" step="1" />
              <span class="field-hint">{{ fields[name]?.source }}</span>
            </label>
          </div>
        </section>

        <section v-show="activeSection === 'search'" class="settings-section">
          <div class="rule-heading"><span class="overline">SEARCH COVERAGE</span></div>
          <h2>搜索范围</h2>
          <p>这些参数控制每次检索纳入的来源规模，不会改变用户已确认的研究对象和分析维度。</p>
          <div class="field-grid">
            <label v-for="name in fieldNames('搜索范围')" :key="name" class="field">
              <span class="field-label-row">
                <span>{{ fieldMeta(name).label }}</span>
                <span class="info-tip">
                  <button type="button" class="info-button" :aria-label="`${fieldMeta(name).label}说明`">!</button>
                  <span class="info-bubble" role="tooltip">{{ fieldMeta(name).tip }}</span>
                </span>
              </span>
              <input v-model.number="form[name]" class="input" type="number" min="1" step="1" />
              <span class="field-hint">{{ fields[name]?.source }}</span>
            </label>
          </div>
        </section>

        <footer class="save-bar">
          <p v-if="saveError" class="settings-error" role="alert">{{ saveError }}</p>
          <p v-else-if="saveNotice" class="save-notice">{{ saveNotice }}</p>
          <span v-else>修改后的设置仅在新建研究任务时生效。</span>
          <button class="btn btn--primary" type="submit" :disabled="!dirty || settingsStore.saving">{{ settingsStore.saving ? '保存中…' : '保存设置' }}</button>
        </footer>
      </form>
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
.settings-page { max-width: 1300px; }
.settings-intro { max-width: 760px; margin: -8px 0 28px; color: var(--ink-soft); }
.settings-layout { display: grid; grid-template-columns: 168px minmax(0, 1fr); gap: 20px; align-items: start; }
.settings-nav { position: sticky; top: 20px; display: grid; padding: 10px; border-radius: var(--radius-lg); }
.settings-nav-item { padding: 10px 11px; text-align: left; border: 0; border-left: 3px solid transparent; background: transparent; color: var(--ink-soft); font-weight: 700; }
.settings-nav-item:hover, .settings-nav-item--active { background: var(--paper-dim); color: var(--ink); border-left-color: var(--vermilion); }
.settings-sheet { min-width: 0; padding: 28px; }
.settings-section h2 { margin: 12px 0 7px; font-family: var(--font-serif); font-size: 22px; line-height: 1.25; }
.settings-section > p, .section-note { color: var(--ink-soft); font-size: 13.5px; margin-bottom: 22px; }
.subheading { margin: 0 0 14px; font-family: var(--font-serif); font-size: 18px; }
.settings-divider { height: 1px; margin: 26px 0 22px; background: var(--line); }
.connection-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.connection-card { display: grid; gap: 14px; min-width: 0; padding: 18px; border: 1px solid var(--line-strong); background: var(--paper-dim); }
.connection-card__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.connection-card__head h3 { margin-top: 5px; font-family: var(--font-serif); font-size: 19px; }
.connection-card__actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding-top: 2px; }

.field-grid, .model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 16px; }
.field-label-row, .credential-title { display: flex; align-items: center; gap: 7px; min-width: 0; margin-bottom: 6px; font-size: 14px; overflow-wrap: anywhere; }
.info-tip { position: relative; display: inline-flex; flex: 0 0 auto; }
.info-button { display: grid; place-items: center; width: 17px; height: 17px; padding: 0; border: 1px solid var(--ink-faint); border-radius: 50%; background: var(--paper-raised); color: var(--ink-soft); font-family: var(--font-sans); font-size: 11px; font-weight: 800; line-height: 1; cursor: help; }
.info-button:hover, .info-button:focus-visible { border-color: var(--vermilion); color: var(--vermilion); outline: none; }
.info-bubble { position: absolute; z-index: 5; bottom: calc(100% + 8px); left: 50%; width: min(270px, calc(100vw - 44px)); padding: 9px 10px; border: 1px solid var(--ink); border-radius: var(--radius); background: var(--ink); color: var(--paper); box-shadow: 3px 3px 0 rgba(43, 39, 35, .18); font-size: 12px; font-weight: 400; line-height: 1.55; opacity: 0; pointer-events: none; transform: translateX(-50%) translateY(3px); transition: opacity .14s ease, transform .14s ease; }
.info-tip:hover .info-bubble, .info-tip:focus-within .info-bubble { opacity: 1; transform: translateX(-50%) translateY(0); }
.model-input-row { display: grid; grid-template-columns: minmax(0, 1fr) 38px; gap: 7px; }
.icon-action { display: grid; place-items: center; width: 38px; height: 40px; border: 1.5px solid var(--ink); border-radius: var(--radius); background: var(--ink); color: var(--paper); font-size: 18px; line-height: 1; }
.icon-action:hover:not(:disabled) { background: var(--vermilion); }
.icon-action:disabled, .text-action:disabled { opacity: .45; cursor: not-allowed; }
.field-hint, .credential-meta { color: var(--ink-faint); font-size: 11px; overflow-wrap: anywhere; }
.credential-list { display: grid; border-top: 1px solid var(--line); }
.credential-row { padding: 16px 0; border-bottom: 1px solid var(--line); }
.credential-title { margin-bottom: 3px; font-family: var(--font-serif); font-size: 16px; }
.credential-state { margin-left: auto; padding: 2px 7px; border: 1px solid var(--line-strong); color: var(--ink-faint); font-family: var(--font-sans); font-size: 11px; white-space: nowrap; }
.credential-state--ready { border-color: var(--forest); color: var(--forest); }
.credential-input-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 12px; }
.credential-input-row .btn { padding: 8px 13px; }
.credential-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 10px; }
.text-action { border: 0; background: transparent; color: var(--ink-soft); font-size: 12.5px; text-decoration: underline; text-underline-offset: 3px; }
.text-action:hover:not(:disabled) { color: var(--vermilion); }
.test-result { color: var(--vermilion-deep); font-size: 12px; overflow-wrap: anywhere; }
.test-result--ok, .save-notice { color: var(--forest); }
.save-bar { position: sticky; bottom: 10px; display: flex; justify-content: space-between; align-items: center; gap: 16px; margin: 30px -4px -4px; padding: 13px 14px; border-top: 1px solid var(--line-strong); background: var(--paper-raised); color: var(--ink-soft); font-size: 13px; }
.settings-error, .settings-warning { padding: 10px 12px; border-left: 3px solid var(--vermilion); background: var(--vermilion-wash); color: var(--vermilion-deep); font-size: 13px; }
.settings-warning { margin: 0 0 16px; border-color: var(--amber); background: var(--amber-wash); color: var(--ink); }
.loading-sheet { padding: 30px; color: var(--ink-soft); }
@media (max-width: 760px) {
  .settings-layout { grid-template-columns: 1fr; }
  .settings-nav { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .settings-sheet { padding: 20px 16px; }
  .connection-grid, .field-grid, .model-grid { grid-template-columns: 1fr; }
  .credential-input-row { grid-template-columns: 1fr; }
  .save-bar { align-items: stretch; flex-direction: column; }
  .info-bubble { top: calc(100% + 8px); bottom: auto; left: 0; right: auto; width: min(270px, calc(100vw - 76px)); transform: translateX(0) translateY(-3px); }
  .info-tip:hover .info-bubble, .info-tip:focus-within .info-bubble { transform: translateX(0) translateY(0); }
}
</style>
