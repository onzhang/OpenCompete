export const STATUS_LABELS = {
  draft: '草稿',
  awaiting_confirmation: '等待确认',
  scope_frozen: '范围已冻结',
  researching: '研究中',
  synthesizing: '报告生成中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
}

export const LEVEL_LABELS = {
  product_service: '产品与服务',
  company_organization: '公司与组织',
  industry_market: '产业与市场',
}

export const NODE_LABELS = {
  prepare_scope: '解析请求',
  discovery: '发现候选竞品',
  confirm: '用户确认',
  freeze_scope: '冻结范围',
  write_research_brief: '生成研究简报',
  supervisor: '规划研究任务',
  researcher: '搜索与证据收集',
  compress_research: '整理研究证据',
  final_report_generation: '生成最终报告',
  materialize: '校验并发布报告',
  validate: '硬门禁校验',
}

export const CLAIM_STATUS_LABELS = {
  supported: '有证据支持',
  conflicted: '证据冲突',
  unknown: '未知',
}

export function shortId(runId) {
  return runId ? runId.slice(0, 8) : ''
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtClock(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  return `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}`
}

export function fmtTokens(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export function fmtUsd(n) {
  if (n == null) return '—'
  return `$${n.toFixed(2)}`
}

export function relativeTime(iso) {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return fmtDateTime(iso).slice(0, 10)
}
