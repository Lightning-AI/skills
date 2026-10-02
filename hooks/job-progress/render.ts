// The bars, drawn from the poller's state files. A port of lightning-jobs/job_progress/statusline.py
// and core.py: keep the two in step, so the band and the status line say the same thing.

import type { BandRow } from '../../types'

export type Stage = { name: string; start: number; end: number | null; attempt?: number }

// The fields of a state/<run>.json file the bars read; the poller writes many more.
export type RunState = {
  run: string
  phase: string
  step: number | null
  total: number | null
  epoch: number | null
  peak: number | null
  peak_epoch: number | null
  eta_s: number | null
  stage: string | null
  stage_since: number | null
  stage_index: number | null
  stage_count: number | null
  stages: Stage[]
  attempt_no: number
  setbacks: unknown[]
  lost_s: number
  last_error: string | null
  last_sample_at: number | null
  issue_since: number | null
  pending_since: number | null
  pending_note: string | null
  started_at: number | null
  finished_at: number | null
  updated_at: number | null
  cost: number | null
}

export type Row = BandRow

export const BAR_WIDTH = 20
export const FINAL_PHASES = ['done', 'failed', 'stopped', 'abandoned']
export const STATE_STALE_AFTER = 60
export const FINAL_VISIBLE_FOR = 600
export const MAX_ROWS = 10

const ICONS: Record<string, string> = {
  pending: '⏳',
  starting: '▶',
  running: '▶',
  recovering: '⟳',
  stalled: '⚠',
  waiting: '✖',
  done: '✔',
  failed: '✖',
  stopped: '■',
  abandoned: '✖',
}

export const isFinal = (s: RunState): boolean => FINAL_PHASES.includes(s.phase)

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '…'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

export function pct(step: number | null, total: number | null): number | null {
  if (step === null || !total) return null
  return Math.max(0, Math.min(100, Math.floor((100 * step) / total)))
}

const pad3 = (n: number | null): string => String(n ?? 'None').padStart(3)

// Python rounds halves to even; match it so the bars agree to the cell.
function roundHalfEven(x: number): number {
  const r = Math.round(x)
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r
}

/** `▓` done now, `▒` ground lost to a setback (current → peak), `░` still to do. */
export function bar(step: number, peak: number, total: number, width = BAR_WIDTH): string {
  const cur = Math.max(0, Math.min(width, roundHalfEven((width * step) / total)))
  const top = Math.max(cur, Math.min(width, roundHalfEven((width * peak) / total)))
  return '▓'.repeat(cur) + '▒'.repeat(top - cur) + '░'.repeat(width - top)
}

function stageTrack(s: RunState, now: number, keep = 3): string {
  const stages = s.stages ?? []
  const parts = stages
    .slice(-(keep + 1))
    .map(st =>
      st.end === null ? `▸ ${st.name} ${fmtDuration(now - st.start)}` : `${st.name} ✔ ${fmtDuration(st.end - st.start)}`,
    )
  if (stages.length > keep + 1) parts.unshift('…')
  return parts.join(' · ')
}

function jobFraction(s: RunState): number | null {
  if (!s.stage_count) return null
  const frac = s.total && s.step !== null ? s.step / s.total : 0
  return Math.max(0, Math.min(1, ((s.stage_index || 1) - 1 + Math.min(frac, 1)) / s.stage_count))
}

const peakOf = (s: RunState): number | null => (s.peak_epoch === s.epoch ? s.peak : s.step)
const isStale = (s: RunState, now: number): boolean =>
  !isFinal(s) && !!s.updated_at && now - s.updated_at > STATE_STALE_AFTER

export function renderLine(s: RunState, now: number): Row {
  const { phase, run, step, total, stage } = s
  const peak = peakOf(s)
  const live = !isFinal(s)
  const stageOnly = !!stage && !total && ['starting', 'running', 'recovering'].includes(phase)
  let head = `${ICONS[phase] ?? '?'} ${run}` + (stage && live && !stageOnly ? ` [${stage}]` : '')
  if (stageOnly && s.stage_count) {
    const done = Math.max(0, Math.min((s.stage_index || 1) - 1, s.stage_count))
    head += `  ${bar(done, done, s.stage_count)}  stage ${s.stage_index}/${s.stage_count}`
  }
  if (total) {
    head += `  ${bar(step || 0, peak || step || 0, total)}  ${pad3(pct(step, total))}%`
    if (s.epoch !== null) head += ` ep${s.epoch}`
    if (s.setbacks.length) head += ` ↺${s.setbacks.length}`
  }
  const since = (t: number | null) => (t ? fmtDuration(now - t) : '…')
  const cause = s.last_error
  let tail: string
  if (phase === 'pending') {
    tail = `${s.pending_note || 'waiting for machine'} · ${since(s.pending_since)}`
  } else if (stageOnly) {
    tail = stageTrack(s, now)
  } else if (phase === 'starting' || (['running', 'recovering'].includes(phase) && !total)) {
    tail = `${stage || 'starting'} · ${since(s.stage_since || s.started_at)}`
  } else if (['running', 'recovering'].includes(phase) && step !== null && total && step >= total) {
    tail = `${stage ? stage + ' ' : ''}finished · ${since(s.last_sample_at)} ago`
  } else if (['running', 'recovering'].includes(phase)) {
    tail = `ETA ${fmtDuration(s.eta_s)}`
    if (s.lost_s >= 1) tail += ` (+${fmtDuration(s.lost_s)})`
    if (phase === 'recovering' && peak && step !== null && peak > step) tail += ` · peak ${pct(peak, total)}%`
  } else if (phase === 'stalled') {
    tail = `stalled ${since(s.last_sample_at)}` + (cause ? ` · ${cause.slice(0, 50)}` : '')
  } else if (phase === 'waiting') {
    tail = `failed · waiting for relaunch ${since(s.issue_since)}` + (cause ? ` · ${cause.slice(0, 40)}` : '')
  } else if (phase === 'done') {
    tail = `done in ${fmtDuration((s.finished_at || now) - (s.started_at || now))}`
    if (s.setbacks.length) tail += ` · setbacks cost ${fmtDuration(s.lost_s)}`
  } else {
    tail = phase
  }
  if (s.cost !== null && s.cost !== undefined && phase !== 'pending') tail += ` · $${s.cost.toFixed(2)}`
  const stale = isStale(s, now)
  return { text: `${head}  ${tail}` + (stale ? ' · stale (poller not running?)' : ''), isStale: stale }
}

/** A running run with stages: a header row with the whole-job bar, then one row per stage of the
 * current attempt. Anything else, or `expand` false, is the single summary line. */
export function renderRows(s: RunState, now: number, expand = true): Row[] {
  const attempt = s.attempt_no || 1
  const stages = (s.stages ?? []).filter(st => (st.attempt ?? 1) === attempt)
  if (!expand || !stages.length || isFinal(s)) return [renderLine(s, now)]
  const since = (t: number | null) => (t ? fmtDuration(now - t) : '…')
  const { phase } = s
  let head = `${ICONS[phase] ?? '?'} ${s.run}`
  const frac = jobFraction(s)
  if (frac !== null) {
    const done = roundHalfEven(frac * 1000)
    head += `  ${bar(done, done, 1000)}  ${pad3(Math.floor(frac * 100))}%`
  }
  const info: string[] = []
  if (s.stage_count) info.push(`stage ${s.stage_index}/${s.stage_count}`)
  if (attempt > 1) info.push(`attempt ${attempt}`)
  if (s.setbacks.length) info.push(`↺${s.setbacks.length}` + (s.lost_s >= 1 ? ` (+${fmtDuration(s.lost_s)})` : ''))
  const cause = s.last_error
  if (phase === 'pending') info.push(`${s.pending_note || 'waiting for machine'} ${since(s.pending_since)}`)
  else if (phase === 'stalled') info.push(`stalled ${since(s.last_sample_at)}` + (cause ? ` · ${cause.slice(0, 50)}` : ''))
  else if (phase === 'waiting')
    info.push(`failed · waiting for relaunch ${since(s.issue_since)}` + (cause ? ` · ${cause.slice(0, 40)}` : ''))
  if (s.cost !== null && s.cost !== undefined) info.push(`$${s.cost.toFixed(2)}`)
  const stale = isStale(s, now)
  const rows: Row[] = [
    { text: head + (info.length ? '  ' + info.join(' · ') : '') + (stale ? ' · stale (poller not running?)' : ''), isStale: stale },
  ]

  const width = Math.max(...stages.map(st => st.name.length))
  for (const st of stages) {
    const name = st.name.padEnd(width)
    if (st.end !== null) {
      rows.push({ text: `   ✔ ${name}  ${fmtDuration(st.end - st.start)}`, isStale: false })
      continue
    }
    let row = `   ▸ ${name}`
    const { step, total } = s
    if (total && st.name === s.stage) {
      const peak = peakOf(s)
      row += `  ${bar(step || 0, peak || step || 0, total)}  ${pad3(pct(step, total))}%`
      if (step !== null && step >= total) {
        row += `  finished · ${since(s.last_sample_at)} ago`
      } else {
        row += `  ETA ${fmtDuration(s.eta_s)}`
        if (peak && step !== null && peak > step) row += ` · peak ${pct(peak, total)}%`
      }
    } else {
      // no bar until the stage's first reading; say so, so an empty row doesn't look broken
      row += `  no progress reported yet · ${since(st.start)}`
    }
    rows.push({ text: row, isStale: false })
  }
  if (s.stage_count && stages.length < s.stage_count) {
    const left = s.stage_count - Math.max(stages.length, s.stage_index || 0)
    if (left > 0) rows.push({ text: `   · ${left} more stage${left > 1 ? 's' : ''}`, isStale: false })
  }
  return rows
}

/** Finished runs linger for a while; live ones show while their poller keeps the state fresh. The
 * status line also keeps a quiet run whose poller is alive; the mod can't check a pid, and a live
 * poller rewrites its state every few seconds anyway. */
export function isShown(s: RunState, now: number): boolean {
  if (isFinal(s)) return now - (s.finished_at || 0) < FINAL_VISIBLE_FOR
  return now - (s.updated_at || 0) < FINAL_VISIBLE_FOR
}

/** Live runs above finished ones, then oldest start first, then by name. */
export function byDisplayOrder(a: RunState, b: RunState): number {
  const key = (s: RunState) => [isFinal(s) ? 1 : 0, s.started_at ?? Infinity] as const
  const [fa, sa] = key(a)
  const [fb, sb] = key(b)
  return fa - fb || (sa === sb ? 0 : sa < sb ? -1 : 1) || a.run.localeCompare(b.run)
}

/** The rows for every shown run: the first two expanded, the rest one line each. */
export function renderAll(states: RunState[], now: number): Row[] {
  const visible = states.filter(s => isShown(s, now)).sort(byDisplayOrder)
  const rows: Row[] = []
  visible.slice(0, 5).forEach((s, i) => {
    let out = renderRows(s, now, i < 2)
    if (rows.length && rows.length + out.length > MAX_ROWS) out = [renderLine(s, now)]
    rows.push(...out)
  })
  return rows.slice(0, MAX_ROWS)
}
