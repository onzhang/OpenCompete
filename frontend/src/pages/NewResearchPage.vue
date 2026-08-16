<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useRunsStore } from '../stores/runs.js'
import TagInput from '../components/TagInput.vue'
import { fmtTokens, fmtUsd } from '../utils/format.js'

const router = useRouter()
const store = useRunsStore()

onMounted(() => {
  store.fetchConfig()
})

const budgetLimit = computed(() => store.budgetLimit)

const form = reactive({
  query: '',
  entities: [],
  focusPoints: [],
  exclusions: [],
  allowedUrls: [],
  discover: true,
})

const submitted = ref(false)
const submitting = ref(false)
const submitError = ref(null)

const ticketNo = `OC-${new Date().getFullYear()}${String(new Date().getMonth() + 1).padStart(2, '0')}${String(new Date().getDate()).padStart(2, '0')}-${Math.floor(Math.random() * 900 + 100)}`
const todayLine = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

function validateUrl(value) {
  if (!/^https?:\/\/.+/.test(value)) {
    return '只接受公开的 http(s):// 网址'
  }
  return null
}

const errors = computed(() => {
  const list = []
  if (submitted.value) {
    if (!form.query.trim()) list.push('请填写研究问题')
    if (!form.entities.length) list.push('至少提供一个研究对象')
  }
  return list
})

const canSubmit = computed(
  () => form.query.trim() && form.entities.length,
)

async function submit() {
  submitted.value = true
  submitError.value = null
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    const runId = await store.createRun({
      query: form.query.trim(),
      entities: form.entities,
      focusPoints: form.focusPoints,
      exclusions: form.exclusions,
      allowedUrls: form.allowedUrls,
      discover: form.discover,
    })
    router.push(`/runs/${runId}`)
  } catch (err) {
    submitError.value = err.message
  } finally {
    submitting.value = false
  }
}

const pipelinePreview = computed(() => {
  const steps = ['理解研究委托', '规划任务相关范围']
  if (form.discover) steps.push('发现候选竞品', '候选确认')
  steps.push('范围确认冻结', '并行深度研究', '生成报告', '门禁校验发布')
  return steps
})
</script>

<template>
  <div class="page">
    <header class="page-head rise-in">
      <span class="overline overline--accent">RESEARCH COMMISSION</span>
      <h1 class="page-title">研究<em>委托单</em></h1>
    </header>

    <div class="layout">
      <!-- 委托单主体 -->
      <form class="ticket sheet rise-in" style="animation-delay: 0.08s" @submit.prevent="submit">
        <div class="ticket-head">
          <div>
            <div class="ticket-no mono">№ {{ ticketNo }}</div>
            <div class="ticket-date">{{ todayLine }}</div>
          </div>
          <div class="ticket-seal" aria-hidden="true">竞品<br />研究</div>
        </div>

        <!-- 研究问题 -->
        <div class="field">
          <label class="field-label" for="query">
            研究问题 <span class="req">＊必填</span>
          </label>
          <textarea
            id="query"
            v-model="form.query"
            class="textarea query-input"
            rows="3"
            placeholder="例：中型 SaaS 企业选型智能客服平台，Intercom、Zendesk 与美洽如何取舍？"
          ></textarea>
          <div class="field-hint">写清楚决策场景与关注点，研究简报将围绕它展开。</div>
        </div>

        <!-- 研究对象 -->
        <div class="field">
          <label class="field-label">
            研究对象 <span class="req">＊至少一个</span>
          </label>
          <TagInput
            v-model="form.entities"
            accent
            placeholder="输入竞品名称后回车，可用逗号批量添加"
          />
          <div class="field-hint">已知的竞品、公司或产品。开启「候选发现」后系统会补充建议对象。</div>
        </div>

        <!-- 可选关注重点：只表达用户意图，不在创建页伪造固定研究矩阵 -->
        <div class="field">
          <label class="field-label">希望重点回答 <span class="optional">可选</span></label>
          <TagInput
            v-model="form.focusPoints"
            placeholder="例：Agent 编程流程、代码库上下文、个人版配额"
          />
          <div class="field-hint">
            这里填写关注点即可。系统会按本次委托动态规划中文研究层级与维度，并在下一步交给你逐项编辑确认。
          </div>
        </div>

        <!-- 排除项 / 网址 -->
        <div class="field-row">
          <div class="field">
            <label class="field-label">排除项</label>
            <TagInput v-model="form.exclusions" placeholder="例：不研究硬件产品线" />
          </div>
          <div class="field">
            <label class="field-label">指定参考网址</label>
            <TagInput
              v-model="form.allowedUrls"
              placeholder="https:// 开头的公开网址"
              :validate="validateUrl"
            />
          </div>
        </div>

        <!-- 候选发现开关 -->
        <div class="discover" :class="{ 'discover--on': form.discover }">
          <button
            type="button"
            class="switch"
            role="switch"
            :aria-checked="form.discover"
            @click="form.discover = !form.discover"
          >
            <span class="switch-knob"></span>
          </button>
          <div class="discover-text">
            <b>研究开始前先发现候选竞品</b>
            <span>系统检索潜在竞争对象并附上来源，由你决定是否纳入范围（消耗少量搜索额度）。</span>
          </div>
        </div>

        <!-- 错误与提交 -->
        <div v-if="errors.length" class="errors">
          <div v-for="msg in errors" :key="msg">· {{ msg }}</div>
        </div>
        <div v-if="submitError" class="errors">
          <div>· 提交失败：{{ submitError }}</div>
        </div>

        <div class="ticket-foot">
          <div class="ticket-foot-note">
            提交后先进行轻量范围规划——<br />范围经你确认冻结后才启动深度研究。
          </div>
          <button type="submit" class="btn btn--primary" :disabled="(submitted && !canSubmit) || submitting">
            {{ submitting ? '正在创建任务…' : '提交委托并草拟范围' }}
            <svg v-if="!submitting" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
          </button>
        </div>
      </form>

      <!-- 侧栏说明 -->
      <aside class="side">
        <div class="side-card sheet rise-in" style="animation-delay: 0.14s">
          <div class="overline side-title">提交后将经过</div>
          <ol class="preview">
            <li v-for="(step, i) in pipelinePreview" :key="step">
              <span class="preview-n mono">{{ i + 1 }}</span>{{ step }}
            </li>
          </ol>
        </div>

        <div class="side-card sheet rise-in" style="animation-delay: 0.2s">
          <div class="overline side-title">本次任务预算上限</div>
          <div v-if="budgetLimit" class="budget-rows">
            <div class="budget-row"><span>输入 tokens</span><i class="dotted-lead"></i><b class="mono">{{ fmtTokens(budgetLimit.input_tokens) }}</b></div>
            <div class="budget-row"><span>输出 tokens</span><i class="dotted-lead"></i><b class="mono">{{ fmtTokens(budgetLimit.output_tokens) }}</b></div>
            <div class="budget-row"><span>Tavily 检索</span><i class="dotted-lead"></i><b class="mono">{{ budgetLimit.tavily_calls }} 次</b></div>
            <div class="budget-row"><span>成本上限</span><i class="dotted-lead"></i><b class="mono">{{ fmtUsd(budgetLimit.cost_usd) }}</b></div>
          </div>
          <div v-else class="budget-rows budget-rows--empty">后端未连接，无法读取预算配置。</div>
          <p class="side-note">
            预算耗尽时任务停止新增外部调用，并以「部分完成」发布已有成果，不会静默超支。
          </p>
        </div>

        <div class="side-card side-card--ink rise-in" style="animation-delay: 0.26s">
          <div class="side-ink-title">证据优先</div>
          <p>
            每条结论都会绑定证据与来源；无法证实的结论会明确标记为「未知」而不是编造。
          </p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 1fr 300px;
  gap: 30px;
  align-items: start;
}

/* 委托单 */
.ticket {
  padding: 30px 36px 28px;
}

.ticket-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 3px double var(--line-ink);
  padding-bottom: 18px;
  margin-bottom: 26px;
}

.ticket-no {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.ticket-date {
  font-size: 12.5px;
  color: var(--ink-faint);
  margin-top: 4px;
}

.ticket-seal {
  width: 58px;
  height: 58px;
  border: 2.5px solid var(--vermilion);
  border-radius: 50%;
  display: grid;
  place-items: center;
  text-align: center;
  font-family: var(--font-serif);
  font-size: 13px;
  font-weight: 900;
  line-height: 1.25;
  color: var(--vermilion);
  transform: rotate(8deg);
  opacity: 0.85;
  mix-blend-mode: multiply;
}

.field {
  margin-bottom: 24px;
}

.query-input {
  font-family: var(--font-serif);
  font-size: 16px;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.optional {
  margin-left: 6px;
  color: var(--ink-ghost);
  font-size: 11px;
  font-weight: 500;
}

/* 发现开关 */
.discover {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 15px 18px;
  border: 1.5px dashed var(--line-strong);
  border-radius: var(--radius);
  margin-bottom: 22px;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.discover--on {
  border-color: var(--forest);
  border-style: solid;
  background: var(--forest-wash);
}

.switch {
  flex-shrink: 0;
  width: 44px;
  height: 24px;
  border-radius: 12px;
  border: 1.5px solid var(--line-strong);
  background: var(--paper-dim);
  position: relative;
  transition: background 0.2s ease, border-color 0.2s ease;
  margin-top: 2px;
}

.discover--on .switch {
  background: var(--forest);
  border-color: var(--forest);
}

.switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: var(--paper-raised);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
  transition: transform 0.2s cubic-bezier(0.22, 0.8, 0.3, 1);
}

.discover--on .switch-knob {
  transform: translateX(19px);
}

.discover-text b {
  display: block;
  font-size: 14px;
}

.discover-text span {
  display: block;
  font-size: 12.5px;
  color: var(--ink-soft);
  margin-top: 3px;
}

.errors {
  background: var(--vermilion-wash);
  border-left: 3px solid var(--vermilion);
  border-radius: var(--radius);
  padding: 12px 16px;
  font-size: 13px;
  color: var(--vermilion-deep);
  margin-bottom: 20px;
  line-height: 1.9;
}

.ticket-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  border-top: 1px solid var(--line);
  padding-top: 20px;
}

.ticket-foot-note {
  font-size: 12px;
  color: var(--ink-faint);
  line-height: 1.7;
}

/* 侧栏 */
.side {
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: sticky;
  top: 30px;
}

.side-card {
  padding: 18px 20px;
}

.side-title {
  margin-bottom: 12px;
}

.preview {
  list-style: none;
  display: flex;
  flex-direction: column;
}

.preview li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  padding: 6px 0;
  border-bottom: 1px dotted var(--line);
}

.preview li:last-child {
  border-bottom: none;
  font-weight: 700;
}

.preview-n {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 10.5px;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  color: var(--ink-faint);
}

.budget-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.budget-rows--empty {
  font-size: 12px;
  color: var(--ink-faint);
}

.budget-row {
  display: flex;
  align-items: baseline;
  font-size: 13px;
}

.budget-row b {
  font-weight: 600;
}

.side-note {
  margin-top: 14px;
  font-size: 12px;
  color: var(--ink-faint);
  line-height: 1.7;
}

.side-card--ink {
  background: var(--ink);
  color: var(--paper);
  border-radius: var(--radius-lg);
  padding: 20px 22px;
}

.side-ink-title {
  font-family: var(--font-serif);
  font-size: 17px;
  font-weight: 900;
  margin-bottom: 8px;
  color: #e8b48e;
}

.side-card--ink p {
  font-size: 12.5px;
  line-height: 1.8;
  color: rgba(244, 238, 225, 0.78);
}

@media (max-width: 1080px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .side {
    position: static;
  }
}
</style>
