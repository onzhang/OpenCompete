// 后端 API 客户端：原生 fetch 封装 + SSE 地址构建。
// 所有请求经 Vite dev proxy（/api → 127.0.0.1:8123）转发。

const BASE = '/api'

const READ_TIMEOUT_MS = 15_000
const ACTION_TIMEOUT_MS = 30_000
// 创建任务包含动态范围规划，开启候选发现时还包含一次检索，给它单独的长时限。
const CREATE_TIMEOUT_MS = 120_000

export class ApiError extends Error {
  constructor(status, body) {
    const err = body?.error || {}
    super(err.message || `请求失败（HTTP ${status}）`)
    this.status = status
    this.code = err.code || 'unknown_error'
    this.category = err.category || null
    this.retryable = !!err.retryable
  }
}

async function request(path, { method = 'GET', body, timeoutMs = READ_TIMEOUT_MS } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let resp
  let data = null
  try {
    resp = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    try {
      data = await resp.json()
    } catch (err) {
      if (controller.signal.aborted) throw err
      data = null
    }
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(0, {
        error: {
          code: 'request_timeout',
          message: '请求等待超时，已停止等待；请核对任务最新状态后重试',
          retryable: true,
        },
      })
    }
    throw new ApiError(0, {
      error: { code: 'network_error', message: '无法连接后端服务，请确认 opencompete-api 已启动', retryable: true },
    })
  } finally {
    clearTimeout(timer)
  }
  if (!resp.ok) throw new ApiError(resp.status, data)
  return data
}

export const api = {
  health: () => request('/health'),
  config: () => request('/config'),
  settings: () => request('/settings'),
  updateSettings: (body) => request('/settings', { method: 'PUT', body, timeoutMs: ACTION_TIMEOUT_MS }),
  updateCredential: (body) => request('/settings/credentials', { method: 'PUT', body, timeoutMs: ACTION_TIMEOUT_MS }),
  testConnection: (target) => request(`/settings/connections/${target}/test`, { method: 'POST', timeoutMs: ACTION_TIMEOUT_MS }),
  revealConnectionKey: (role) => request(`/settings/connections/${role}/api-key`),
  listRuns: () => request('/runs'),
  getRun: (runId) => request(`/runs/${runId}`),
  getBundle: (runId) => request(`/runs/${runId}/bundle`),
  createRun: (body) => request('/runs', { method: 'POST', body, timeoutMs: CREATE_TIMEOUT_MS }),
  confirmCandidates: (runId, body) =>
    request(`/runs/${runId}/candidate-selection`, {
      method: 'POST',
      body,
      timeoutMs: ACTION_TIMEOUT_MS,
    }),
  confirmScope: (runId, body) =>
    request(`/runs/${runId}/scope-confirmation`, {
      method: 'POST',
      body,
      timeoutMs: ACTION_TIMEOUT_MS,
    }),
  resumeRun: (runId) =>
    request(`/runs/${runId}/resume`, { method: 'POST', timeoutMs: ACTION_TIMEOUT_MS }),
  cancelRun: (runId) =>
    request(`/runs/${runId}/cancel`, { method: 'POST', timeoutMs: ACTION_TIMEOUT_MS }),
  deleteRun: (runId) =>
    request(`/runs/${runId}`, { method: 'DELETE', timeoutMs: ACTION_TIMEOUT_MS }),
}

export function eventsUrl(runId, after = 0) {
  return `${BASE}/runs/${runId}/events?after=${after}`
}
