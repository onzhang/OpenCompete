<script setup>
import { computed } from 'vue'
import { useRunsStore } from '../stores/runs.js'
import StatusStamp from '../components/StatusStamp.vue'
import {
  STATUS_LABELS,
  shortId,
  relativeTime,
  fmtUsd,
} from '../utils/format.js'

const store = useRunsStore()

const today = new Date()
const dateLine = `${today.getFullYear()} 年 ${today.getMonth() + 1} 月 ${today.getDate()} 日`

const activeRuns = computed(() => store.activeRuns)
const published = computed(() => store.publishedRuns.slice(0, 4))

const stats = computed(() => {
  const runs = store.runs
  const completed = runs.filter((r) => r.status === 'completed')
  const totalCost = runs.reduce((s, r) => s + (r.budget_spent_usd || 0), 0)
  const avgRace =
    completed.filter((r) => r.race_overall).reduce((s, r) => s + r.race_overall, 0) /
    (completed.filter((r) => r.race_overall).length || 1)
  return [
    { label: '累计研究任务', value: String(runs.length), unit: '项' },
    { label: '已发布报告', value: String(completed.length), unit: '份' },
    { label: 'RACE 平均分', value: avgRace ? avgRace.toFixed(1) : '—', unit: '/100' },
    { label: '累计成本', value: totalCost.toFixed(2), unit: 'USD' },
  ]
})

const flowSteps = [
  { n: '01', title: '提交研究委托', desc: '研究问题 · 对象 · 层级维度' },
  { n: '02', title: '确认研究范围', desc: '候选竞品选择 · 范围冻结' },
  { n: '03', title: '并行深度研究', desc: 'Supervisor 分派 · 证据采集' },
  { n: '04', title: '发布结构化报告', desc: '证据链校验 · RACE 评测' },
]

function progressHint(run) {
  if (run.status === 'awaiting_confirmation') {
    return run.confirmation_kind === 'candidate_selection'
      ? '等待你确认候选竞品'
      : '等待你确认研究范围'
  }
  if (run.status === 'researching') return '研究单元执行中'
  if (run.status === 'synthesizing') return '正在撰写最终报告'
  return STATUS_LABELS[run.status]
}

function queryOf(run) {
  return run.query || '（未记录研究问题）'
}
</script>

<template>
  <div class="page">
    <!-- 刊头 -->
    <header class="masthead rise-in">
      <div class="masthead-top">
        <span class="overline overline--accent">OpenCompete · 本地竞品研究档案</span>
        <span class="overline">{{ dateLine }}</span>
      </div>
      <h1 class="page-title">
        研究<em>工作台</em>
      </h1>
      <p class="masthead-sub">
        从一个研究问题出发：确认范围 → 并行取证 → 结构化报告。每一条结论都可追溯到来源。
      </p>
    </header>

    <!-- 后端连接状态 -->
    <div v-if="store.runsError" class="conn-error rise-in">
      <b>无法读取任务数据：</b>{{ store.runsError }}
      <button class="conn-retry" @click="store.fetchRuns()">重试</button>
    </div>

    <!-- 统计行 -->
    <section class="stats rise-in" style="animation-delay: 0.06s">
      <div v-for="s in stats" :key="s.label" class="stat">
        <div class="stat-value">
          <span class="stat-num">{{ s.value }}</span>
          <span class="stat-unit mono">{{ s.unit }}</span>
        </div>
        <div class="stat-label">{{ s.label }}</div>
      </div>
    </section>

    <div class="grid">
      <!-- 左列 -->
      <div class="col-main">
        <!-- 进行中卷宗 -->
        <section class="rise-in" style="animation-delay: 0.12s">
          <div class="rule-heading">
            <span class="overline overline--accent">进行中的研究</span>
          </div>
          <div v-if="activeRuns.length" class="active-list">
            <RouterLink
              v-for="run in activeRuns"
              :key="run.run_id"
              :to="`/runs/${run.run_id}`"
              class="active-card sheet"
            >
              <div class="active-card-top">
                <StatusStamp :status="run.status" size="sm" />
                <span class="mono active-id">{{ shortId(run.run_id) }}</span>
                <span class="active-time">{{ relativeTime(run.updated_at) }}</span>
              </div>
              <div class="active-query">{{ queryOf(run) }}</div>
              <div class="active-foot">
                <span class="active-entities">{{ run.entities.join(' · ') }}</span>
                <span class="active-hint" :class="{ 'active-hint--action': run.status === 'awaiting_confirmation' }">
                  {{ progressHint(run) }}
                  <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
                </span>
              </div>
            </RouterLink>
          </div>
          <div v-else class="empty sheet">当前没有进行中的研究任务。</div>
        </section>

        <!-- 最近发布 -->
        <section class="rise-in" style="animation-delay: 0.18s">
          <div class="rule-heading">
            <span class="overline overline--accent">最近发布的报告</span>
            <RouterLink to="/runs" class="see-all">查看全部 →</RouterLink>
          </div>
          <div class="pub-list">
            <RouterLink
              v-for="(run, i) in published"
              :key="run.run_id"
              :to="`/runs/${run.run_id}/report`"
              class="pub-item"
            >
              <span class="pub-index mono">{{ String(i + 1).padStart(2, '0') }}</span>
              <span class="pub-body">
                <span class="pub-title">{{ queryOf(run) }}</span>
                <span class="pub-meta">
                  {{ run.entities.join('、') }}
                  <span class="pub-sep">·</span>
                  {{ relativeTime(run.updated_at) }}
                  <span class="pub-sep">·</span>
                  成本 {{ fmtUsd(run.budget_spent_usd) }}
                </span>
              </span>
              <span v-if="run.race_overall" class="pub-score">
                <b class="mono">{{ run.race_overall.toFixed(1) }}</b>
                <i>RACE</i>
              </span>
              <StatusStamp v-else :status="run.status" size="sm" />
            </RouterLink>
          </div>
        </section>
      </div>

      <!-- 右列 -->
      <aside class="col-side">
        <!-- 发起研究 -->
        <RouterLink to="/new" class="cta pressed rise-in" style="animation-delay: 0.1s">
          <div class="cta-mark" aria-hidden="true">
            <svg viewBox="0 0 44 44" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="19" cy="19" r="10" />
              <path d="M27 27 37 37" stroke-linecap="round" />
              <path d="M15 19h8M19 15v8" stroke-linecap="round" />
            </svg>
          </div>
          <div class="cta-title">发起新研究</div>
          <p class="cta-desc">填写研究委托单：问题、对象与分析层级。系统草拟范围后由你确认冻结。</p>
          <span class="cta-go">开始填写 →</span>
        </RouterLink>

        <!-- 工作流程说明 -->
        <div class="flow sheet rise-in" style="animation-delay: 0.16s">
          <div class="flow-head overline">系统如何工作</div>
          <div v-for="step in flowSteps" :key="step.n" class="flow-step">
            <span class="flow-n mono">{{ step.n }}</span>
            <span class="flow-body">
              <b>{{ step.title }}</b>
              <i>{{ step.desc }}</i>
            </span>
          </div>
          <div class="flow-note">
            全程本地运行 · 预算硬上限 · 报告与证据原子发布
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
/* 刊头 */
.masthead {
  border-bottom: 3px double var(--line-ink);
  padding-bottom: 22px;
  margin-bottom: 24px;
}

.masthead-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 4px;
}

.masthead-sub {
  margin-top: 10px;
  font-size: 14.5px;
  color: var(--ink-soft);
  max-width: 560px;
}

/* 后端错误提示 */
.conn-error {
  margin-bottom: 20px;
  padding: 12px 18px;
  background: var(--vermilion-wash);
  border-left: 3px solid var(--vermilion);
  border-radius: var(--radius);
  font-size: 13px;
  color: var(--vermilion-deep);
  display: flex;
  align-items: center;
  gap: 12px;
}

.conn-retry {
  margin-left: auto;
  font-size: 12px;
  font-weight: 700;
  padding: 3px 14px;
  border: 1.5px solid var(--vermilion);
  border-radius: 3px;
  background: transparent;
  color: var(--vermilion-deep);
}

/* 统计 */
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border: 1.5px solid var(--ink);
  border-radius: var(--radius);
  background: var(--paper-raised);
  margin-bottom: 34px;
  overflow: hidden;
}

.stat {
  padding: 16px 20px 14px;
  border-right: 1px solid var(--line);
}

.stat:last-child {
  border-right: none;
}

.stat-num {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
}

.stat-unit {
  font-size: 11px;
  color: var(--ink-faint);
  margin-left: 4px;
}

.stat-label {
  font-size: 12px;
  color: var(--ink-faint);
  margin-top: 3px;
  letter-spacing: 0.04em;
}

/* 布局 */
.grid {
  display: grid;
  grid-template-columns: 1fr 316px;
  gap: 30px;
  align-items: start;
}

.col-main {
  display: flex;
  flex-direction: column;
  gap: 34px;
  min-width: 0;
}

.col-side {
  display: flex;
  flex-direction: column;
  gap: 22px;
  position: sticky;
  top: 30px;
}

.rule-heading {
  margin-bottom: 16px;
}

.see-all {
  font-size: 12.5px;
  color: var(--ink-faint);
  text-decoration: none;
  white-space: nowrap;
}

.see-all:hover {
  color: var(--vermilion);
}

/* 进行中卡片 */
.active-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.active-card {
  display: block;
  padding: 16px 20px;
  text-decoration: none;
  color: inherit;
  transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
}

.active-card:hover {
  transform: translateY(-2px);
  border-color: var(--ink);
  box-shadow: 0 2px 3px rgba(74, 60, 34, 0.08), 0 14px 32px rgba(74, 60, 34, 0.1);
}

.active-card-top {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 9px;
}

.active-id {
  font-size: 11px;
  color: var(--ink-faint);
}

.active-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--ink-ghost);
}

.active-query {
  font-family: var(--font-serif);
  font-size: 16.5px;
  font-weight: 700;
  line-height: 1.5;
}

.active-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 9px;
}

.active-entities {
  font-size: 12.5px;
  color: var(--ink-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.active-hint {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
  white-space: nowrap;
}

.active-hint--action {
  color: var(--vermilion);
}

.empty {
  padding: 26px;
  text-align: center;
  color: var(--ink-ghost);
  font-size: 13.5px;
}

/* 发布列表 —— 头条风 */
.pub-list {
  display: flex;
  flex-direction: column;
}

.pub-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 15px 6px;
  border-bottom: 1px solid var(--line);
  text-decoration: none;
  color: inherit;
  transition: background 0.15s ease;
}

.pub-item:hover {
  background: var(--paper-raised);
}

.pub-item:hover .pub-title {
  color: var(--vermilion);
}

.pub-index {
  font-size: 13px;
  color: var(--ink-ghost);
  flex-shrink: 0;
  width: 26px;
}

.pub-body {
  min-width: 0;
  flex: 1;
}

.pub-title {
  display: block;
  font-family: var(--font-serif);
  font-size: 15.5px;
  font-weight: 700;
  line-height: 1.5;
  transition: color 0.15s ease;
}

.pub-meta {
  display: block;
  font-size: 12px;
  color: var(--ink-faint);
  margin-top: 3px;
}

.pub-sep {
  margin: 0 5px;
  color: var(--line-strong);
}

.pub-score {
  flex-shrink: 0;
  text-align: right;
}

.pub-score b {
  display: block;
  font-size: 19px;
  font-weight: 600;
  color: var(--forest);
}

.pub-score i {
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 9px;
  letter-spacing: 0.2em;
  color: var(--ink-ghost);
}

/* 发起研究 CTA */
.cta {
  display: block;
  padding: 24px 24px 22px;
  text-decoration: none;
  color: inherit;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.cta:hover {
  transform: translate(-2px, -2px);
  box-shadow: 6px 6px 0 var(--ink);
}

.cta-mark {
  width: 46px;
  height: 46px;
  color: var(--vermilion);
  margin-bottom: 14px;
}

.cta-title {
  font-family: var(--font-serif);
  font-size: 21px;
  font-weight: 900;
}

.cta-desc {
  font-size: 13px;
  color: var(--ink-soft);
  margin-top: 8px;
  line-height: 1.7;
}

.cta-go {
  display: inline-block;
  margin-top: 14px;
  font-weight: 700;
  font-size: 14px;
  color: var(--vermilion);
}

/* 工作流程 */
.flow {
  padding: 20px 22px;
}

.flow-head {
  margin-bottom: 14px;
}

.flow-step {
  display: flex;
  gap: 13px;
  padding: 9px 0;
  border-bottom: 1px dotted var(--line-strong);
}

.flow-step:last-of-type {
  border-bottom: none;
}

.flow-n {
  font-size: 12px;
  color: var(--vermilion);
  font-weight: 600;
  padding-top: 2px;
}

.flow-body b {
  display: block;
  font-size: 13.5px;
  font-weight: 700;
}

.flow-body i {
  display: block;
  font-style: normal;
  font-size: 12px;
  color: var(--ink-faint);
  margin-top: 2px;
}

.flow-note {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 2px solid var(--ink);
  font-size: 11.5px;
  color: var(--ink-faint);
  letter-spacing: 0.03em;
}

@media (max-width: 1080px) {
  .grid {
    grid-template-columns: 1fr;
  }
  .col-side {
    position: static;
  }
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
  .stat {
    border-bottom: 1px solid var(--line);
  }
}
</style>
