/**
 * df-observer - Client half (the human-facing panel + Chat alert rows).
 *
 * Rules obeyed (references/ui-plugin.md, practices.md, the shipped slot catalog):
 *   - plain JS, React from the module table, no imports, no host primitives;
 *   - registrations wrapped in `ctx.slots.inject(ownerKey, () => ctx.slots.register(...))`;
 *   - styling ONLY through theme tokens (`--dsw-alias-*`) delivered as React inline
 *     styles - the static Client half has no `styles.insert` builtin (measured:
 *     `styles` belongs to the dynamic runner), and inline token styles are how the
 *     shipped local panels do it;
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

    const PANEL_ID = '@local/df-observer/panel'
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
      'ladder.next': 'NEXT',
      'todos': 'To-dos',
      'todos.open': 'open',
      'todos.done': 'done',
      'alerts': 'Alerts',
      'alerts.none': 'no active alerts',
      'alerts.ack': 'Ack',
      'alerts.acked': 'acked',
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
      'ladder.next': '下一步',
      'todos': '待办',
      'todos.open': '未完成',
      'todos.done': '已完成',
      'alerts': '警报',
      'alerts.none': '没有活动警报',
      'alerts.ack': '确认',
      'alerts.acked': '已确认',
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
    }

    /** Theme-token-only styles; literal colors stay out of everything but artwork. */
    const S = {
      panel: { display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 12px', margin: '0 auto',
        maxWidth: 720, border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 10,
        background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-primary)',
        fontSize: 12.5, lineHeight: 1.45 },
      head: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
      title: { fontWeight: 600 },
      meta: { display: 'flex', gap: 8, flexWrap: 'wrap', color: 'var(--dsw-alias-label-secondary)' },
      sec: { borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 6 },
      secTitle: { color: 'var(--dsw-alias-label-secondary)', fontSize: 11.5, letterSpacing: '.04em',
        textTransform: 'uppercase', marginBottom: 4 },
      bar: { display: 'inline-flex', gap: 3, verticalAlign: 'middle' },
      rung: { width: 9, height: 9, borderRadius: 2, background: 'var(--dsw-alias-state-idle-primary)' },
      line: { display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap' },
      ids: { color: 'var(--dsw-alias-label-secondary)', minWidth: 28 },
      ev: { color: 'var(--dsw-alias-label-secondary)', wordBreak: 'break-word' },
      note: { color: 'var(--dsw-alias-label-secondary)' },
      body: { display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 340, overflow: 'auto' },
      ack: { appearance: 'none', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 6,
        background: 'transparent', color: 'var(--dsw-alias-label-primary)', font: 'inherit',
        fontSize: 11, padding: '1px 7px', cursor: 'pointer' },
      toggle: { appearance: 'none', border: 'none', background: 'transparent',
        color: 'var(--dsw-alias-label-secondary)', font: 'inherit', cursor: 'pointer', padding: 0 },
      alertRow: { border: '1px solid var(--dsw-alias-border-l1)',
        borderLeft: '3px solid var(--dsw-alias-state-warn-primary)', borderRadius: 8,
        padding: '6px 10px', background: 'var(--dsw-alias-bg-layer-1)',
        color: 'var(--dsw-alias-label-primary)', fontSize: 12.5, lineHeight: 1.45,
        display: 'flex', flexDirection: 'column', gap: 3, maxWidth: 720 },
    }

    const DOT = {
      run: 'var(--dsw-alias-state-success-primary)',
      pause: 'var(--dsw-alias-state-warn-primary)',
      off: 'var(--dsw-alias-state-error-primary)',
      idle: 'var(--dsw-alias-state-idle-primary)',
    }

    function t(key) { return UI_EN[key] ?? key }

    function dotColor(s) {
      if (s.online === false) return DOT.off
      if (s.paused === true) return DOT.pause
      if (s.tick > 0) return DOT.run
      return DOT.idle
    }

    function usePanel(ctx, sessionId, pollMs) {
      const [snap, setSnap] = React.useState(null)
      const [error, setError] = React.useState(null)
      React.useEffect(() => {
        let live = true
        const tick = async () => {
          try {
            const value = await ctx.remote.dfObserver.panel(sessionId ?? null)
            if (!live) return
            setSnap(value)
            setError(null)
          } catch (e) {
            if (live) setError(String(e?.message ?? e))
          }
        }
        void tick()
        const timer = setInterval(tick, Math.max(2000, pollMs))
        return () => { live = false; clearInterval(timer) }
      }, [sessionId, pollMs])
      return { snap, error }
    }

    function Panel(ctx) {
      return function DfObserverPanel(props) {
        const sessionId = props.sessionId
        const { snap, error } = usePanel(ctx, sessionId, 5000)
        const [open, setOpen] = React.useState(false)
        if (!snap) {
          return h('div', { style: S.panel },
            h('div', { style: S.head },
              h('span', { style: S.title }, t('title')),
              h('span', { style: S.meta }, error ? `! ${error}` : t('empty'))))
        }
        const done = (snap.rungs ?? []).filter((r) => r.done).length
        const total = (snap.rungs ?? []).length
        const todos = snap.todos ?? []
        const openTodos = todos.filter((x) => x.state !== 'done')
        const alerts = (snap.alerts ?? []).filter((a) => !a.cleared)
        const health = snap.online === false ? t('health.offline')
          : snap.paused === true ? t('health.paused') : t('health.online')
        const boxes = (snap.boxes ?? []).slice(0, 4).map((b, i) =>
          h('div', { key: `b${i}`, style: S.line },
            h('span', { style: S.ids }, `${t('box')} elev${b.elev}`),
            h('span', null, b.to_dig === null
              ? t('noData')
              : `${t('to_dig')} ${b.to_dig} · ${t('dug')} ${b.dug} · ${t('pending')} ${b.pending}`)))
        return h('div', { style: S.panel },
          h('div', { style: S.head },
            h('span', { style: { ...S.rung, borderRadius: '50%', background: dotColor(snap) } }),
            h('span', { style: S.title }, t('title')),
            h('span', { style: S.meta },
              h('span', { key: 'h' }, health),
              snap.pauseSource ? h('span', { key: 'ps' }, t(`health.pauseSource.${snap.pauseSource}`)) : null,
              h('span', { key: 'g' }, `${t('health.gen')} ${snap.generation ?? '?'}`),
              h('span', { key: 'tk' }, `${t('health.tick')} ${snap.tick}`),
              h('span', { key: 'p' }, `${t('health.poll')} ${Math.round((snap.pollIntervalMs ?? 0) / 1000)}s`),
              h('span', { key: 'pa' }, `${t('pause.authority')} ${snap.config?.pause_authority ?? 'off'}`),
              snap.filter?.enabled
                ? h('span', { key: 'fl' }, `filter ${snap.filter.live ? 'live' : 'spawning'}${snap.filter.verdictTick != null ? ` @${snap.filter.verdictTick}` : ''}${snap.filter.fallback ? ' (fallback)' : ''}`)
                : null,
              snap.sessionFolded ? h('span', { key: 'sf' }, t('session.folded')) : null),
            h('button', {
              style: S.toggle, type: 'button', 'aria-expanded': open,
              onClick: () => setOpen((v) => !v),
            }, open ? '▾' : '▸'),
            h('span', { style: S.bar, title: `${done}/${total}` },
              (snap.rungs ?? []).map((r) => h('span', {
                key: r.id,
                style: { ...S.rung, background: r.done ? DOT.run : DOT.pause },
                title: r.title,
              })))),
          open ? h('div', { style: S.body },
            snap.next ? h('div', { style: S.sec },
              h('div', { style: S.secTitle }, t('ladder.next')),
              h('div', { style: S.line }, h('span', null, `${snap.next.id ?? ''} ${snap.next.title ?? ''}`)),
              snap.next.at ? h('div', { style: S.ev },
                `x${snap.next.at.x0}-${snap.next.at.x1} y${snap.next.at.y0}-${snap.next.at.y1} elev${snap.next.at.elev}`) : null) : null,
            boxes.length ? h('div', { style: S.sec }, h('div', { style: S.secTitle }, t('box')), ...boxes) : null,
            h('div', { style: S.sec },
              h('div', { style: S.secTitle },
                `${t('todos')} (${openTodos.length} ${t('todos.open')}, ${todos.length - openTodos.length} ${t('todos.done')})`),
              openTodos.slice(0, 10).map((todo) => h('div', { key: `t${todo.id}`, style: S.line },
                h('span', { style: S.ids }, `#${todo.id}`),
                h('span', null, String(todo.title ?? '')),
                h('span', { style: S.ev }, String(todo.evidence ?? '').slice(0, 90))))),
            h('div', { style: S.sec },
              h('div', { style: S.secTitle }, t('alerts')),
              alerts.length
                ? alerts.slice(0, 8).map((a) => h('div', { key: a.id, style: S.line },
                  h('span', { style: S.ids }, a.id),
                  h('span', {
                    style: { color: a.acked ? 'var(--dsw-alias-label-secondary)'
                      : (a.kind === 'water' || a.kind === 'game_crashed' || a.kind === 'damp_hot')
                        ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-state-warn-primary)',
                      textDecoration: a.acked ? 'line-through' : 'none' },
                  }, `${a.kind}${a.coord ? ` [${a.coord}]` : ''}`),
                  h('span', { style: S.ev }, String(a.evidence).slice(0, 110)),
                  h('button', {
                    style: S.ack, type: 'button', disabled: !!a.acked,
                    onClick: () => { void ctx.remote.dfObserver.ackAlert(a.id).then(() => { }) },
                  }, a.acked ? t('alerts.acked') : t('alerts.ack'))))
                : h('div', { style: S.note }, t('alerts.none'))),
            (snap.notes ?? []).length
              ? h('div', { style: S.sec },
                h('div', { style: S.secTitle }, t('notes')),
                (snap.notes ?? []).slice(0, 6).map((n, i) => h('div', { key: i, style: S.note }, String(n).slice(0, 140))))
              : null)
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
      kind: ALERT_KIND,
      target: 'chat',
      match: (event) => {
        if (event.type !== 'user/message') return null
        const payload = parseObserver(event.data.content)
        if (!payload) return null
        return { id: String(event.seq), role: 'start' }
      },
      start: (_context, match) => {
        const payload = parseObserver(match.event.data.content)
        return { alerts: (payload?.data?.alerts ?? []).slice(0, 6), tick: payload?.tick ?? null }
      },
      update: (context) => context.state,
      buildViewNode: (context) => {
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
        const data = props.node?.data
        if (!data?.alerts?.length) return null
        const first = data.alerts[0]
        return h('div', { style: S.alertRow },
          h('div', { style: S.head },
            h('span', { style: { fontWeight: 600 } }, String(first.kind)),
            first.coord ? h('span', { style: S.ev }, String(first.coord)) : null,
            h('span', { style: S.ev }, `${first.id}${data.alerts.length > 1 ? ` +${data.alerts.length - 1}` : ''}`)),
          h('div', null, String(first.evidence ?? '')),
          first.suggested ? h('div', { style: S.ev }, `→ ${first.suggested}`) : null)
      }
    }

    // Context PROPERTIES, not service ids - and the `remote` face needs the
    // DEEP path of every namespace the module reads (measured: declaring bare
    // 'remote' leaves ctx.remote.dfObserver undefined; 'remote.dfObserver' works,
    // the same way dsh-dox declares 'remote.workspaceFiles').
    return {
      inject: ['slots', 'uiConversation', 'remote', 'remote.dfObserver', 'locale'],
      apply(ctx) {
        ctx.effect(() => ctx.locale.register(NS, { en: UI_EN, zh: UI_ZH }), 'df-observer: dictionaries')
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
