/**
 * df-observer - Host transport to the DF MCP server.
 *
 * The plugin's poller reads ONE composite facts payload per tick from
 * `GET /observe` (schema df-observer/v1, the repo's own MCP server, see
 * main.py). The transport pattern is the one proven by scripts/dfw.py in this
 * repo, re-expressed for Node:
 *
 *   - retry with exponential backoff while the game is offline;
 *   - announce "game offline" (the caller logs it; the observer state marks it);
 *   - tolerate long game-off periods (offline_grace, default 1 h);
 *   - 4xx is OUR bug - never retried, raised with the path for diagnosis;
 *   - connection/timeout/5xx while :5000 is down = the game is closed.
 *
 * Elevations are IN-GAME panel numbers everywhere (z = elev + 129). The
 * transport never mutates the game: it issues GET only. The single contemplated
 * write (pause) is set-pause-state and is index-only here, Config-gated by the
 * owner, default OFF.
 *
 * @module @local/df-observer/observer
 */

export const OBSERVE_SCHEMA = 'df-observer/v1'

/** Raised when the game has been offline longer than the grace window. */
export class GameOffline extends Error {
  constructor(message, { path } = {}) {
    super(message)
    this.name = 'GameOffline'
    this.path = path
  }
}

/** Transport failure that is ours (4xx, JSON shape): surface it, never hide it in retries. */
export class ObserverBug extends Error {
  constructor(message, { path, status } = {}) {
    super(message)
    this.name = 'ObserverBug'
    this.path = path
    this.status = status
  }
}

function isAbortError(error) {
  return error?.name === 'AbortError' || error?.name === 'TimeoutAborted'
}

/**
 * One HTTP JSON exchange with exponential backoff and offline accounting.
 *
 * Mirrors scripts/dfw.py: a connection failure is the game being closed; a 4xx
 * is our request being wrong; 5xx/timeout is retried; a 429 is retried like a
 * backoff signal.
 */
export async function httpJson({ base, path, params, method = 'GET', body, timeoutMs = 20000 }) {
  const url = new URL(path, base)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, typeof value === 'string' ? value : JSON.stringify(value))
    }
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new Error('TimeoutAborted')), timeoutMs)
  try {
    const response = await fetch(url, {
      method,
      headers: body === undefined ? {} : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    const text = await response.text()
    if (!response.ok) {
      const err = new Error(`HTTP ${response.status} ${path}: ${text.slice(0, 240)}`)
      err.status = response.status
      throw err
    }
    try {
      return JSON.parse(text)
    } catch {
      throw new ObserverBug(`non-JSON answer from ${path}: ${text.slice(0, 160)}`, { path })
    }
  } finally {
    clearTimeout(timer)
  }
}

/**
 * The observer client: owns the offline window, the retry nap ladder, and the
 * `:5000` probe that separates "game closed" from "our bug".
 */
export class ObserverTransport {
  /**
   * @param config - {base, timeoutMs, offlineGraceMs, napMinMs, napMaxMs, announceEveryMs}
   * @param note - (message) => void, sink for "game offline" announcements.
   */
  constructor(config, note = () => {}) {
    this.base = config.base
    this.timeoutMs = config.timeoutMs
    this.offlineGraceMs = config.offlineGraceMs
    this.napMinMs = config.napMinMs
    this.napMaxMs = config.napMaxMs
    this.announceEveryMs = config.announceEveryMs
    this.note = note
    /** null while healthy; {since, failures, lastNote} while the game is offline. */
    this.offline = null
  }

  get offlineSince() {
    return this.offline?.since ?? null
  }

  /** Observe the game: one GET /observe with the caller's request object. */
  async observe(request) {
    return httpJson({ base: this.base, path: '/observe', params: { q: request } })
  }

  /** TCP reachability of DFHack's RPC port - separates "closed" from "broken". */
  async probeRpc(port = 5000, host = '127.0.0.1') {
    let net
    try {
      net = (await import('node:net')).default
    } catch {
      return true // no net module: assume reachable, /observe will tell the truth
    }
    return new Promise((resolve) => {
      const socket = net.createConnection({ port, host })
      const done = (value) => {
        socket.destroy()
        resolve(value)
      }
      socket.setTimeout(1500)
      socket.once('connect', () => done(true))
      socket.once('timeout', () => done(false))
      socket.once('error', () => done(false))
    })
  }

  /**
   * Pause (true) / resume (false) - THE ONLY WRITE THIS PLUGIN CAN ISSUE, and
   * only when its owner passed the Config gate in index.js.
   */
  async setPause(paused) {
    return httpJson({ base: this.base, path: '/get-pause-state', method: 'GET' }).then((state) =>
      httpJson({ base: this.base, path: '/set-pause-state', method: 'POST', body: { paused: Boolean(paused) } })
        .then((answer) => ({ ...answer, before: state.paused })))
  }

  /**
   * Observe with retries. Returns
   *   { ok: true, data, wasOffline, offlineMs }      - on success,
   *   { ok: false, offline: true, offlineMs, lastError, bug } - game still offline,
   *   { ok: false, bug: ObserverBug }                - our request was wrong (no retry).
   * Never throws for the game being down; the poller is a long-lived loop.
   */
  async withRetry(run, { maxMs = 120000 } = {}) {
    const startedAt = Date.now()
    let nap = Math.max(200, this.napMinMs)
    let lastError = null
    for (;;) {
      try {
        const data = await run()
        const wasOffline = this.offline !== null
        const offlineMs = wasOffline ? Date.now() - this.offline.since : 0
        this.offline = null
        return { ok: true, data, wasOffline, offlineMs }
      } catch (error) {
        if (error instanceof ObserverBug) return { ok: false, bug: error }
        const status = error?.status
        if (typeof status === 'number' && status >= 400 && status < 500 && status !== 429) {
          return { ok: false, bug: new ObserverBug(String(error?.message ?? error), { status }) }
        }
        lastError = error
        if (this.offline === null) this.offline = { since: Date.now(), failures: 0, lastNote: 0 }
        this.offline.failures += 1
        const waited = Date.now() - this.offline.since
        if (waited > this.offlineGraceMs) {
          const gaveUp = new GameOffline(
            `game offline ${Math.round(waited / 1000)}s: ${lastError?.message ?? lastError}`, {})
          this.offline.lastNote = Date.now()
          this.note(`df-observer: ${gaveUp.message}`)
          return { ok: false, offline: true, offlineMs: waited, lastError: gaveUp }
        }
        if (Date.now() - this.offline.lastNote > this.announceEveryMs) {
          this.offline.lastNote = Date.now()
          this.note(`df-observer: game offline (${Math.round(waited / 1000)}s), retrying: ${lastError?.message ?? lastError}`)
        }
        if (Date.now() - startedAt > maxMs) {
          return { ok: false, offline: true, offlineMs: waited, lastError }
        }
        await sleep(Math.min(nap, this.napMaxMs))
        nap = Math.min(nap * 2, this.napMaxMs)
      }
    }
  }
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
