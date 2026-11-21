/**
 * df-observer - Client half (the human-facing panel + Chat alert rows).
 *
 * Rules obeyed (references/ui-plugin.md, practices.md, the shipped slot catalog):
 *   - plain JS, React from the module table, no imports, no host primitives;
 *   - registrations wrapped in `ctx.slots.inject(ownerKey, () => ctx.slots.register(...))`;
 *   - styling through `styles.insert(css)` and theme tokens ONLY (`--dsw-alias-*`) -
 *     light/dark comes from the host, no literal colors outside state dots' mapping;
 *   - Chat rows through `ctx.uiConversation.events.register()` with the view keyed
 *     by the Definition's kind in `conversation.chat.node`;
 *   - game/session state read from the Host half through the package's `panel` Remote.
 *
 * @module @local/df-observer/client
 */
window.__ModuleLoader__.load({
  id: '@local/df-observer',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    const PANEL_ID = '@deepseek-ai/dsh-client-ui-df-observer/panel'
    const ALERT_KIND = 'df-alert'
    const NS = 'dfObserver'

    const UI_EN = {
      'title': 'Fort observer',
      'health.online': 'running',
      'health.paused': 'paused',
      'health.offline': 'game offline',
      'health.gen': 'gen',
      'health.tick': 'tick',
      'health.poll': 'poll',
      'health.pauseSource.plugin': 'last pause: plugin',
      'health.pauseSource.game-auto-or-player': 'last pause: game/player',
      'health.pauseSource.player-resume': 'resumed by the player',
      'ladder': 'Ladder',
      'ladder.next': 'NEXT',
      'todos': 'To-dos',
      'todos.open': 'open',
      'todos.done': 'done',
      'alerts': 'Alerts',
      'alerts.none': 'no active alerts',
      'alerts.ack': 'Ack',
      'alerts.acked': 'acked',
      'alerts.more': 'more in df_alerts',
      'notes': 'Notes',
      'collapsed': 'Observer',
      'empty': 'Observer is idle: no game read yet.',
      'box': 'box',
      'to_dig': 'to_dig',
      'dug': 'dug',
      'pending': 'pending',
      'noData': 'NO DATA',
      'pause.authority': 'pause',
      'session.folded': 'session log folded',
      'todo.why': 'evidence',
    }

    const UI_ZH = {
      'title': '堡垒观察器',
      'health.online': '运行中',
      'health.paused': '已暂停',
      'health.offline': '游戏离线',
      'health.gen': '世代',
      'health.tick': '轮次',
      'health.poll': '轮询',
      'health.pauseSource.plugin': '上次暂停：插件',
      'health.pauseSource.game-auto-or-player': '上次暂停：游戏/玩家',
      'health.pauseSource.player-resume': '玩家已恢复',
      'ladder': '阶梯',
      'ladder.next': '下一步',
      'todos': '待办',
      'todos.open': '未完成',
      'todos.done': '已完成',
      'alerts': '警报',
      'alerts.none': '没有活动警报',
      'alerts.ack': '确认',
      'alerts.acked': '已确认',
      'alerts.more': '更多见 df_alerts',
      'notes': '备注',
      'collapsed': '观察器',
      'empty': '观察器空闲：尚无游戏读取。',
      'box': '区块',
      'to_dig': '待挖',
      'dug': '已挖',
      'pending': '排队',
      'noData': '无数据',
      'pause.authority': '暂停权',
      'session.folded': '会话日志已折叠',
      'todo.why': '证据',
    }

    const CSS = `
.dfo-panel { display: flex; flex-direction: column; gap: 6px; padding: 8px 12px; margin: 0 auto;
  max-width: 720px; border: 1px solid var(--dsw-alias-border-l1); border-radius: 10px;
  background: var(--dsw-alias-bg-layer-1); color: var(--dsw-alias-label-primary);
  font-size: 12.5px; line-height: 1.45; }
.dfo-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dfo-title { font-weight: 600; }
.dfo-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dfo-dot-run { background: var(--dsw-alias-state-success-primary); }
.dfo-dot-pause { background: var(--dsw-alias-state-warn-primary); }
.dfo-dot-off { background: var(--dsw-alias-state-error-primary); }
.dfo-dot-idle { background: var(--dsw-alias-state-idle-primary); }
.dfo-meta { color: var(--dsw-alias-label-secondary); display: flex; gap: 8px; flex-wrap: wrap; }
.dfo-sec { border-top: 1px solid var(--dsw-alias-border-l1); padding-top: 6px; }
.dfo-sec-title { color: var(--dsw-alias-label-secondary); font-size: 11.5px; letter-spacing: .04em;
  text-transform: uppercase; margin-bottom: 4px; }
.dfo-bar { display: inline-flex; gap: 3px; vertical-align: middle; }
.dfo-rung { width: 9px; height: 9px; border-radius: 2px; background: var(--dsw-alias-state-idle-primary); }
.dfo-rung-done { background: var(--dsw-alias-state-success-primary); }
.dfo-rung-open { background: var(--dsw-alias-state-warn-primary); }
.dfo-line { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
.dfo-ids { color: var(--dsw-alias-label-secondary); min-width: 28px; }
.dfo-ev { color: var(--dsw-alias-label-secondary); word-break: break-word; }
.dfo-alert { color: var(--dsw-alias-state-warn-primary); }
.dfo-alert-crit { color: var(--dsw-alias-state-error-primary); }
.dfo-acked { color: var(--dsw-alias-label-secondary); text-decoration: line-through; }
.dfo-ack { border: 1px solid var(--dsw-alias-border-l2); border-radius: 6px; background: transparent;
  color: var(--dsw-alias-label-primary); font: inherit; font-size: 11px; padding: 1px 7px; cursor: pointer; }
.dfo-ack:hover { background: var(--dsw-alias-bg-layer-2); }
.dfo-ack:focus-visible { outline: 2px solid var(--dsw-alias-brand-primary); }
.dfo-toggle { border: none; background: transparent; color: var(--dsw-alias-label-secondary);
  font: inherit; cursor: pointer; padding: 0; }
.dfo-toggle:focus-visible { outline: 2px solid var(--dsw-alias-brand-primary); }
.dfo-body { display: flex; flex-direction: column; gap: 8px; max-height: 340px; overflow: auto; }
.dfo-chip { border: 1px solid var(--dsw-alias-border-l1); border-radius: 999px; padding: 0 7px;
  color: var(--dsw-alias-label-secondary); }
.dfo-note { color: var(--dsw-alias-label-secondary); }
.dfo-alert-row { border: 1px solid var(--dsw-alias-border-l1); border-left: 3px solid var(--dsw-alias-state-warn-primary);
  border-radius: 8px; padding: 6px 10px; background: var(--dsw-alias-bg-layer-1);
  color: var(--dsw-alias-label-primary); font-size: 12.5px; line-height: 1.45;
  display: flex; flex-direction: column; gap: 3px; max-width: 720px; }
.dfo-alert-row .head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.dfo-alert-row .kind { font-weight: 600; }
.dfo-alert-row .coord { color: var(--dsw-alias-label-secondary); }
`

    function t(key) {
      return (UI_EN[key] ?? key)
    }

    function dotClass(s) {
      if (s.online === false) return 'dfo-dot dfo-dot-off'
      if (s.paused === true) return 'dfo-dot dfo-dot-pause'
      if (s.tick > 0) return 'dfo-dot dfo-dot-run'
      return 'dfo-dot dfo-dot-idle'
    }

    function usePanel(ctx, sessionId, pollMs) {
      const [snap, setSnap] = React.useState(null)
      const [error, setError] = React.useState(null)
      React.useEffect(() => {
        let live = true
        const tick = async () => {
          try {
            const value = await ctx.remote.dfObserver.panel(sessionId ?? null)
            if (live) {
              setSnap(value)
              setError(null)
            }
          } catch (e) {
            if (live) setError(String(e?.message ?? e))
          }
        }
        void tick()
        const timer = setInterval(tick, Math.max(2000, pollMs))
        return () => {
          live = false
          clearInterval(timer)
        }
      }, [sessionId])
      return { snap, error }
    }

    function Panel(ctx) {
      return function DfObserverPanel(props) {
        const sessionId = props.sessionId
        const { snap, error } = usePanel(ctx, sessionId, snap?.config?.panel_poll_ms ?? 5000)
        const [open, setOpen] = React.useState(false)
        if (!snap) {
          return h('div', { class: 'dfo-panel' },
            h('div', { class: 'dfo-head' },
              h('span', { class: 'dfo-title' }, t('title')),
              h('span', { class: 'dfo-meta' }, error ? `! ${error}` : t('empty'))))
        }
        const done = (snap.rungs ?? []).filter((r) => r.done).length
        const total = (snap.rungs ?? []).length
        const todos = snap.todos ?? []
        const openTodos = todos.filter((x) => x.state !== 'done')
        const alerts = (snap.alerts ?? []).filter((a) => !a.cleared)
        const health = snap.online === false ? t('health.offline')
          : snap.paused === true ? t('health.paused') : t('health.online')
        const boxLines = (snap.boxes ?? []).slice(0, 4).map((b) =>
          h('div', { class: 'dfo-line', key: `b${b.elev}${b.box.join(',')}` },
            h('span', { class: 'dfo-ids' }, `${t('box')} elev${b.elev}`),
            h('span', null, b.to_dig === null ? t('noData')
              : `${t('to_dig')} ${b.to_dig} · ${t('dug')} ${b.dug} · ${t('pending')} ${b.pending}`)))
        return h('div', { class: 'dfo-panel' },
          h('div', { class: 'dfo-head' },
            h('span', { class: dotClass(snap) }),
            h('span', { class: 'dfo-title' }, t('title')),
            h('span', { class: 'dfo-meta' },
              health,
              snap.pauseSource ? h('span', { key: 'ps' }, ` ${t(`health.pauseSource.${snap.pauseSource}`) ?? snap.pauseSource}`) : null,
              h('span', { key: 'g' }, `${t('health.gen')} ${snap.generation ?? '?'}`),
              h('span', { key: 'tk' }, `${t('health.tick')} ${snap.tick}`),
              h('span', { key: 'p' }, `${t('health.poll')} ${Math.round((snap.pollIntervalMs ?? 0) / 1000)}s`),
              h('span', { key: 'pa' }, `${t('pause.authority')} ${snap.config?.pause_authority ?? 'off'}`),
              snap.filter?.enabled ? h('span', { key: 'fl' }, `filter ${snap.filter.live ? 'live' : 'spawning'}${snap.filter.verdictTick != null ? ` @${snap.filter.verdictTick}` : ''}${snap.filter.fallback ? ' (fallback)' : ''}`) : null,
              snap.sessionFolded ? h('span', { key: 'sf' }, t('session.folded')) : null),
            h('button', {
              class: 'dfo-toggle', type: 'button', 'aria-expanded': open,
              onClick: () => setOpen((v) => !v),
            }, open ? '▾' : '▸'),
            h('span', { class: 'dfo-bar', title: `${done}/${total}` },
              (snap.rungs ?? []).map((r) => h('span', {
                key: r.id, class: `dfo-rung ${r.done ? 'dfo-rung-done' : 'dfo-rung-open'}`, title: r.title,
              })))),
          open ? h('div', { class: 'dfo-body' },
            snap.next ? h('div', { class: 'dfo-sec' },
              h('div', { class: 'dfo-sec-title' }, t('ladder.next')),
              h('div', { class: 'dfo-line' }, h('span', null, `${snap.next.id}: ${snap.next.title}`)),
              snap.next.at ? h('div', { class: 'dfo-ev' },
                `x${snap.next.at.x0}-${snap.next.at.x1} y${snap.next.at.y0}-${snap.next.at.y1} elev${snap.next.at.elev}`) : null) : null,
            boxLines.length ? h('div', { class: 'dfo-sec' },
              h('div', { class: 'dfo-sec-title' }, t('box')), ...boxLines) : null,
            h('div', { class: 'dfo-sec' },
              h('div', { class: 'dfo-sec-title' }, `${t('todos')} (${openTodos.length} ${t('todos.open')}, ${todos.length - openTodos.length} ${t('todos.done')})`),
              openTodos.slice(0, 10).map((todo) => h('div', { class: 'dfo-line', key: `t${todo.id}` },
                h('span', { class: 'dfo-ids' }, `#${todo.id}`),
                h('span', null, todo.title),
                h('span', { class: 'dfo-ev' }, String(todo.evidence ?? '').slice(0, 90))))),
            h('div', { class: 'dfo-sec' },
              h('div', { class: 'dfo-sec-title' }, t('alerts')),
              alerts.length
                ? alerts.slice(0, 8).map((a) => h('div', { class: 'dfo-line', key: a.id },
                  h('span', { class: 'dfo-ids' }, a.id),
                  h('span', { class: a.acked ? 'dfo-acked' : (a.kind === 'water' || a.kind === 'game_crashed' ? 'dfo-alert-crit' : 'dfo-alert') },
                    `${a.kind}${a.coord ? ` [${a.coord}]` : ''}`),
                  h('span', { class: 'dfo-ev' }, String(a.evidence).slice(0, 110)),
                  h('button', {
                    class: 'dfo-ack', type: 'button', disabled: a.acked,
                    onClick: () => { void ctx.remote.dfObserver.ackAlert(a.id).then(() => { }) } },
                  a.acked ? t('alerts.acked') : t('alerts.ack'))))
                : h('div', { class: 'dfo-note' }, t('alerts.none'))),
            (snap.notes ?? []).length ? h('div', { class: 'dfo-sec' },
              h('div', { class: 'dfo-sec-title' }, t('notes')),
              (snap.notes ?? []).slice(0, 6).map((n, i) => h('div', { class: 'dfo-note', key: i }, String(n).slice(0, 140)))) : null)
            : null)
      }
    }

    function parseObserver(content) {
      const texts = []
      const walk = (value, depth) => {
        if (depth > 4 || value == null) return
        if (typeof value === 'string') { texts.push(value); return }
        if (Array.isArray(value)) { for (const item of value) walk(item, depth + 1); return }
        if (typeof value === 'object') {
          for (const key of ['text', 'output', 'content', 'value']) {
            if (value[key] !== undefined) walk(value[key], depth + 1)
          }
        }
      }
      walk(content, 0)
      for (const text of texts) {
        const line = text.split('\n').find((l) => l.startsWith('[df-observer] '))
        if (!line) continue
        try {
          const payload = JSON.parse(line.slice('[df-observer] '.length))
          return payload?.form === 'alert' ? payload : null
        } catch {
          return null
        }
      }
      return null
    }

    /** Chat rows: one wake-alert message folded into one keyed node. */
    const alertDefinition = {
      'kind': ALERT_KIND,
      'target': 'chat',
      'match': (event) => {
        if (event.type !== 'user/message') return null
        const payload = parseObserver(event.data.content)
        if (!payload) return null
        return { id: String(event.seq), role: 'start' }
      },
      'start': (_context, match) => {
        const payload = parseObserver(match.event.data.content)
        return { alerts: (payload?.data?.alerts ?? []).slice(0, 6), tick: payload?.tick ?? null }
      },
      'update': (context) => context.state,
      'buildViewNode': (context) => {
        if (context.start === undefined || !context.state?.alerts?.length) return null
        return {
          key: context.key,
          kind: ALERT_KIND,
          id: context.id,
          target: 'chat',
          anchorSeq: context.start.event.seq,
          location: context.start.location,
          visibility: 'visible',
          data: { alerts: context.state.alerts, tick: context.state.tick },
        }
      },
    }

    function AlertRow() {
      return function DfAlertRow(props) {
        const node = props.node
        const data = node?.data
        if (!data?.alerts?.length) return null
        const first = data.alerts[0]
        return h('div', { class: 'dfo-alert-row' },
          h('div', { class: 'head' },
            h('span', { class: 'kind' }, `${first.kind}`),
            first.coord ? h('span', { class: 'coord' }, first.coord) : null,
            h('span', { class: 'coord' }, `${first.id}${data.alerts.length > 1 ? ` +${data.alerts.length - 1}` : ''}`)),
          h('div', null, String(first.evidence ?? '')),
          first.suggested ? h('div', { class: 'dfo-ev' }, `→ ${first.suggested}`) : null)
      }
    }

    return {
      'inject': ['slots', 'uiConversation', 'remoteAccess', 'locale'],
      apply(ctx) {
        ctx.effect(() => ctx.locale.register(NS, { en: UI_EN, zh: UI_ZH }), 'df-observer: dictionaries')
        ctx.effect(() => styles.insert(CSS), 'df-observer: styles')
        ctx.effect(() => ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
          name: 'conversation.input.dock', id: PANEL_ID, order: 30,
          label: () => t('collapsed'), locale: NS,
        }, Panel(ctx))), 'df-observer: panel')
        ctx.effect(() => ctx.uiConversation.events.register(alertDefinition), 'df-observer: alert definition')
        ctx.effect(() => ctx.slots.inject('conversation.chat.node', () => ctx.slots.register({
          name: 'conversation.chat.node', key: ALERT_KIND, locale: NS,
        }, AlertRow(ctx))), 'df-observer: alert row')
      },
    }
  },
})
