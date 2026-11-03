/**
 * dsh-hardware-monitor — DSH 宿主插件（服务端半）。
 *
 * 职责：
 *   1. 注册 /hardware-monitor-api 数据通道（同源代理到本机 Hardware Monitor Flask 服务），
 *      让 DSH 渲染进程不受 CORS/混合内容影响；
 *   2. 注册 hardware_monitor_status 工具，让模型可以直接查询本机 CPU/内存/GPU/温度/网络；
 *   3. **自动拉起监控服务**：DSH 启动/插件重载时若 5678 未运行，用仓库 .venv 的 Python
 *      以 --no-elevate 启动（避免 UAC 弹窗），并做 30 秒看门狗重试。
 *
 * 服务地址 / token 通过环境变量配置（在启动 DSH 的终端设置）：
 *   HARDWARE_MONITOR_URL       默认 http://127.0.0.1:5678
 *   HARDWARE_MONITOR_TOKEN     默认空（对应服务端 MONITOR_TOKEN）
 *   HARDWARE_MONITOR_AUTOSTART 设为 0/false 可关闭自动拉起
 *   HARDWARE_MONITOR_REPO      监控仓库目录（默认从插件位置向上推导）
 *   HARDWARE_MONITOR_PYTHON    Python 可执行文件（默认优先 .venv）
 *
 * 无外部依赖，仅用 Node 内置模块。
 */
import { appendFileSync, existsSync, openSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { request } from 'node:http'

const PLUGIN_DIR = fileURLToPath(new URL('..', import.meta.url))
const DEFAULT_SERVICE = 'http://127.0.0.1:5678'
const SERVICE = String(process.env.HARDWARE_MONITOR_URL || DEFAULT_SERVICE).replace(/\/+$/, '')
const TOKEN = String(process.env.HARDWARE_MONITOR_TOKEN || '')
const AUTOSTART = !['0', 'false', 'no', 'off'].includes(
  String(process.env.HARDWARE_MONITOR_AUTOSTART || '1').toLowerCase(),
)
const WATCHDOG_MS = 30_000
const START_COOLDOWN_MS = 15_000

let starting = false
let lastStartAt = 0

function trace(message) {
  try {
    appendFileSync(join(PLUGIN_DIR, 'apply.log'),
      `${new Date().toISOString()} pid=${process.pid} ${message}\n`, 'utf8')
  } catch {
    /* 日志失败不影响功能 */
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 请求 Flask 服务，返回 { status, data }。 */
function httpJson(path, timeoutMs = 4000) {
  const url = new URL(path, SERVICE)
  return new Promise((resolve, reject) => {
    const req = request(url, {
      timeout: timeoutMs,
      headers: {
        Accept: 'application/json',
        ...(TOKEN ? { 'X-Monitor-Token': TOKEN } : {}),
      },
    }, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (chunk) => { body += chunk })
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(body) })
        } catch {
          reject(new Error('invalid JSON response'))
        }
      })
    })
    req.on('timeout', () => req.destroy(new Error('timeout')))
    req.on('error', reject)
    req.end()
  })
}

async function serviceReady() {
  try {
    const result = await httpJson('/api/start', 1200)
    return result.status === 200
  } catch {
    return false
  }
}

/** 从插件位置向上找仓库根目录（含 server.py）。 */
function findRepo() {
  const candidates = [
    process.env.HARDWARE_MONITOR_REPO,
    fileURLToPath(new URL('../../..', import.meta.url)),
    'C:\\Users\\13235\\Desktop\\性能监测\\hardware-monitor-2026-11-03',
  ].filter((item) => typeof item === 'string' && item.length > 0)
  for (const candidate of candidates) {
    try {
      if (existsSync(join(candidate, 'server.py'))) return candidate
    } catch {
      /* 忽略不可访问目录 */
    }
  }
  return ''
}

function findPython(repo) {
  const candidates = [
    process.env.HARDWARE_MONITOR_PYTHON,
    repo ? join(repo, '.venv', 'Scripts', 'python.exe') : '',
    repo ? join(repo, '.venv', 'bin', 'python') : '',
    'python',
  ].filter(Boolean)
  for (const candidate of candidates) {
    try {
      if (candidate === 'python' || existsSync(candidate)) return candidate
    } catch {
      /* 忽略 */
    }
  }
  return 'python'
}

/**
 * 确保监控服务在运行：已运行直接返回；否则用 .venv Python 后台启动。
 * 带 in-flight 去重与启动冷却，避免看门狗/客户端并发触发时重复 spawn。
 */
async function ensureService(reason = 'startup') {
  if (!AUTOSTART) return { running: false, disabled: true }
  if (await serviceReady()) return { running: true }

  if (starting) return { running: false, starting: true }
  const now = Date.now()
  if (now - lastStartAt < START_COOLDOWN_MS) return { running: false, cooldown: true }

  starting = true
  lastStartAt = now
  try {
    const repo = findRepo()
    if (!repo) {
      trace(`autostart skipped: repo not found (reason=${reason})`)
      return { running: false, error: 'repo not found' }
    }
    const python = findPython(repo)
    const logPath = join(PLUGIN_DIR, 'service.log')
    let logFd = 'ignore'
    try { logFd = openSync(logPath, 'a') } catch { /* 日志不可写则丢弃 */ }

    const child = spawn(python, ['-m', 'hardware_monitor', '--no-elevate'], {
      cwd: repo,
      detached: true,
      windowsHide: true,
      stdio: ['ignore', logFd, logFd],
      env: {
        ...process.env,
        MONITOR_NO_ELEVATE: '1',
        ...(TOKEN ? { MONITOR_TOKEN: TOKEN } : {}),
      },
    })
    child.unref()
    trace(`autostart spawn pid=${child.pid} python=${python} repo=${repo} reason=${reason}`)

    for (let attempt = 0; attempt < 20; attempt += 1) {
      await sleep(300)
      if (await serviceReady()) {
        trace(`autostart ready pid=${child.pid} after ~${(attempt + 1) * 300}ms`)
        return { running: true, started: true, pid: child.pid }
      }
    }
    trace(`autostart not ready yet pid=${child.pid}（服务继续后台启动）`)
    return { running: false, started: true, pid: child.pid }
  } catch (error) {
    trace(`autostart failed: ${error?.message || error}`)
    return { running: false, error: String(error?.message || error) }
  } finally {
    starting = false
  }
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  res.end(body)
}

function registerDataRoutes(ctx) {
  const handler = async (req, res) => {
    try {
      const path = String(req.url || '').split('?')[0]
      if (req.method !== 'GET') {
        sendJson(res, 405, { ok: false, error: 'method not allowed' })
        return
      }
      if (path === '/hardware-monitor-api/health') {
        try {
          const result = await httpJson('/api/start')
          sendJson(res, 200, { ok: result.status === 200, status: result.status, data: result.data })
        } catch (error) {
          // 服务可能刚被关掉：后台拉起，下一次轮询即可恢复
          void ensureService('health-route')
          sendJson(res, 503, { ok: false, error: 'service unavailable', detail: String(error?.message || error) })
        }
        return
      }
      if (path === '/hardware-monitor-api/summary') {
        try {
          const result = await httpJson('/api/all')
          if (result.status !== 200) {
            sendJson(res, result.status, { ok: false, status: result.status, data: result.data })
            return
          }
          sendJson(res, 200, { ok: true, base_url: SERVICE, data: result.data })
        } catch (error) {
          void ensureService('summary-route')
          sendJson(res, 503, { ok: false, error: 'service unavailable', detail: String(error?.message || error) })
        }
        return
      }
      if (path === '/hardware-monitor-api/processes') {
        try {
          const result = await httpJson('/api/processes?limit=12')
          if (result.status !== 200) {
            sendJson(res, result.status, { ok: false, status: result.status, data: result.data })
            return
          }
          sendJson(res, 200, result.data)
        } catch (error) {
          void ensureService('processes-route')
          sendJson(res, 503, { ok: false, error: 'service unavailable', detail: String(error?.message || error) })
        }
        return
      }
      sendJson(res, 404, { ok: false, error: 'unknown endpoint' })
    } catch (error) {
      try {
        sendJson(res, 500, { ok: false, error: String(error?.message || error) })
      } catch {
        /* 响应已结束 */
      }
    }
  }
  const dispose = ctx.webServer.register({ kind: 'prefix', path: '/hardware-monitor-api', handler })
  return () => {
    try { if (typeof dispose === 'function') dispose() } catch { /* 忽略 */ }
  }
}

function num(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function summarize(data) {
  data = data || {}
  const temperature = data.temperature || {}
  const cpuTemp = temperature.cpu || temperature.gpu || {}
  return {
    cpu: num((data.cpu || {}).usage),
    mem: num((data.memory || {}).usage_percent),
    gpu: num((data.gpu || {}).usage),
    temp: num(cpuTemp.value),
    download: num((data.network_speed || {}).download_mbps),
    upload: num((data.network_speed || {}).upload_mbps),
    uptime: (data.system_info || {}).uptime || null,
    platform: (data.platform || {}).os || null,
  }
}

function fmt(value, digits = 0) {
  return value === null || value === undefined ? '--' : Number(value).toFixed(digits)
}

function statusTool() {
  return {
    name: 'hardware_monitor_status',
    description: [
      '读取本机 Hardware Monitor 的实时快照：CPU 占用、内存占用、NVIDIA GPU 占用、',
      'CPU/GPU 温度、网络上下行速率、开机时长与平台。',
      '服务未运行或未配置 token 时返回 ok=false（附原因），不要编造数据。',
    ].join(''),
    parameters: { type: 'object', additionalProperties: false, properties: {} },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: value?.text || JSON.stringify(value) }],
    },
    timeoutMs: 15_000,
    async execute() {
      try {
        const result = await httpJson('/api/all', 4000)
        if (result.status === 401) {
          return {
            ok: false,
            code: 'TOKEN_REQUIRED',
            message: 'Hardware Monitor 启用了 MONITOR_TOKEN，但 DSH 插件未配置 HARDWARE_MONITOR_TOKEN',
            text: 'Hardware Monitor 需要 token：在启动 DSH 的终端设置 HARDWARE_MONITOR_TOKEN 后重启。',
          }
        }
        if (result.status !== 200) {
          return {
            ok: false,
            code: 'SERVICE_UNAVAILABLE',
            status: result.status,
            message: `Hardware Monitor 返回 HTTP ${result.status}`,
            text: `Hardware Monitor 不可用（HTTP ${result.status}）：${SERVICE}`,
          }
        }
        const s = summarize(result.data)
        const text = [
          `CPU ${fmt(s.cpu)}% · MEM ${fmt(s.mem)}% · GPU ${fmt(s.gpu)}% · ${fmt(s.temp, 1)}°C`,
          `网络 ↓ ${fmt(s.download, 1)} MB/s ↑ ${fmt(s.upload, 1)} MB/s · 开机 ${s.uptime || '--'} · 平台 ${s.platform || '--'}`,
        ].join('\n')
        return { ok: true, ...s, service: SERVICE, text, snapshot: result.data }
      } catch (error) {
        void ensureService('tool')
        return {
          ok: false,
          code: 'SERVICE_DOWN',
          message: String(error?.message || error),
          text: `Hardware Monitor 服务不可用：${SERVICE}（正在尝试自动启动；也可手动运行 run.bat）`,
        }
      }
    },
  }
}

export const inject = []

export function apply(ctx) {
  trace(`apply service=${SERVICE} token=${TOKEN ? 'set' : 'empty'} autostart=${AUTOSTART}`)

  // 启动时确保监控服务在跑（DSH 重启后挂件/工具自动恢复）；失败不阻塞插件加载。
  ensureService('dsh-startup').then((state) => {
    trace(`startup ensure running=${state.running} started=${state.started || false} ${state.error || ''}`)
  }).catch((error) => trace(`startup ensure failed: ${error?.message || error}`))

  // 看门狗：服务被关掉后 30 秒内自动恢复（unref 不阻塞进程退出）
  try {
    const timer = setInterval(() => {
      ensureService('watchdog').catch((error) => trace(`watchdog failed: ${error?.message || error}`))
    }, WATCHDOG_MS)
    if (typeof timer.unref === 'function') timer.unref()
  } catch {
    /* 忽略 */
  }

  ctx.inject(['webServer'], (wsCtx) => {
    wsCtx.effect(() => registerDataRoutes(wsCtx), 'dsh-hardware-monitor: /hardware-monitor-api routes')
    trace('routes registered: /hardware-monitor-api/{summary,health}')
  })

  ctx.inject(['tools'], (toolCtx) => {
    toolCtx.effect(() => {
      const dispose = toolCtx.tools.register(statusTool())
      trace('tools registered: hardware_monitor_status')
      return () => {
        try { if (typeof dispose === 'function') dispose() } catch { /* 忽略 */ }
      }
    }, 'dsh-hardware-monitor: tools')
  })
}
