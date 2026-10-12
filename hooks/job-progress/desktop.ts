// The desktop app's view of the runs: a card per run, drawn as an SVG bar with a stage timeline and
// a line of text, where the terminal draws the ASCII rows in render.ts. Same state files, same order.

import type { CardStage, RunCard } from '../../types'
import {
  byDisplayOrder,
  fmtDuration,
  ICONS,
  isFinal,
  isShown,
  isStale,
  jobFraction,
  peakOf,
  type RunState,
} from './render'

// a fourth card no longer fits the band collapsed; the rest sit behind a button below the cards
export const MAX_CARDS = 3
export const BAR_W = 480
export const BAR_H = 12

// mid-tone colors that read on the app's light and dark themes alike
const COLOR = {
  track: '#8b8b8b',
  run: '#3b82f6',
  done: '#22c55e',
  lost: '#f59e0b',
  bad: '#ef4444',
}

export type JobRef = RunCard['job']

const RUNNING = ['starting', 'running', 'recovering']

function stagesOf(s: RunState, now: number): CardStage[] {
  // without a declared count the timeline would split the bar evenly over the stages seen so far,
  // so a stage that lasted no time took half of it; the terminal draws those runs as one bar too
  if (!s.stage_count) return []
  const attempt = s.attempt_no || 1
  const seen = (s.stages ?? []).filter(st => (st.attempt ?? 1) === attempt)
  const out: CardStage[] = seen.map(st => {
    const isActive = st.end === null
    const hasReading = isActive && st.name === s.stage && !!s.total && s.step !== null
    return {
      name: st.name,
      // a failed or stopped run's last stage stays the open one, drawn in the run's color
      state: isActive && s.phase !== 'done' ? 'active' : 'done',
      seconds: (st.end ?? now) - st.start,
      fraction: hasReading ? Math.max(0, Math.min(1, s.step! / s.total!)) : null,
    }
  })
  // the stages still to come, named by their number: the job declares only how many there are
  for (let i = out.length; i < (s.stage_count ?? 0); i++) {
    out.push({ name: `stage ${i + 1}`, state: 'todo', seconds: null, fraction: null })
  }
  return out
}

function headlineOf(s: RunState, now: number, fraction: number | null): string {
  const since = (t: number | null) => (t ? fmtDuration(now - t) : '…')
  const parts: string[] = []
  const pctText = fraction !== null ? `${Math.floor(fraction * 100)}%` : null
  switch (s.phase) {
    case 'pending':
      return `${s.pending_note || 'waiting for machine'} · ${since(s.pending_since)}`
    case 'stalled':
      return [pctText, `stalled ${since(s.last_sample_at)}`].filter(Boolean).join(' · ')
    case 'waiting':
      return `failed · waiting for relaunch ${since(s.issue_since)}`
    case 'done': {
      const took = `done in ${fmtDuration((s.finished_at || now) - (s.started_at || now))}`
      return s.setbacks.length ? `${took} · setbacks cost ${fmtDuration(s.lost_s)}` : took
    }
  }
  if (!RUNNING.includes(s.phase)) return s.phase
  if (pctText) parts.push(pctText)
  if (s.total && s.step !== null && s.step >= s.total) parts.push(`${s.stage ? s.stage + ' ' : ''}finished`)
  else if (s.total) parts.push(`ETA ${fmtDuration(s.eta_s)}`)
  else parts.push(`${s.stage || 'starting'} · ${since(s.stage_since || s.started_at)}`)
  if (s.setbacks.length) parts.push(`↺${s.setbacks.length}` + (s.lost_s >= 1 ? ` (+${fmtDuration(s.lost_s)})` : ''))
  return parts.join(' · ')
}

function detailOf(s: RunState, now: number): string | null {
  if ((s.phase === 'stalled' || s.phase === 'waiting') && s.last_error) return s.last_error.slice(0, 160)
  if (s.stage && s.stage_count && !isFinal(s)) {
    const tail = s.total ? '' : ` · no progress reported yet · ${fmtDuration(now - (s.stage_since || now))}`
    return `stage ${s.stage_index}/${s.stage_count}: ${s.stage}${tail}`
  }
  if (isStale(s, now)) return 'stale: is the poller still running?'
  return null
}

export function toCard(s: RunState, job: JobRef, now: number): RunCard {
  const fraction = jobFraction(s) ?? (s.total && s.step !== null ? Math.max(0, Math.min(1, s.step / s.total)) : null)
  const peak = peakOf(s)
  const peakFraction = !s.stage_count && s.total && peak && s.step !== null && peak > s.step ? Math.min(1, peak / s.total) : null
  return {
    run: s.run,
    icon: ICONS[s.phase] ?? '?',
    phase: s.phase,
    isFinal: isFinal(s),
    isStale: isStale(s, now),
    fraction: s.phase === 'done' ? 1 : fraction,
    peak: peakFraction,
    stages: stagesOf(s, now),
    headline: headlineOf(s, now, s.phase === 'done' ? 1 : fraction),
    detail: detailOf(s, now),
    cost: s.cost !== null && s.cost !== undefined ? `$${s.cost.toFixed(2)}` : null,
    job,
  }
}

/** The cards for every shown run, live ones first, as the terminal orders its rows. */
export function toCards(states: RunState[], jobs: Record<string, JobRef>, now: number): RunCard[] {
  return states
    .filter(s => isShown(s, now))
    .sort(byDisplayOrder)
    .map(s => toCard(s, jobs[s.run] ?? null, now))
}

/** The cards the band draws: the first few, or all of them once the person expanded the band. */
export const visibleCards = (all: RunCard[], expanded: boolean): RunCard[] =>
  expanded ? all : all.slice(0, MAX_CARDS)

/** The button under the cards that shows the runs without one, or hides them again. */
export const moreLabel = (hidden: number, expanded: boolean): string =>
  expanded ? 'Show fewer runs' : `Show ${hidden} more run${hidden > 1 ? 's' : ''}`

/** The job's page on Lightning, as the SDK's `Job.link` builds it. */
export function jobUrl(job: NonNullable<JobRef>, cloud = 'https://lightning.ai'): string | null {
  if (!job.teamspace || !job.teamspace.includes('/')) return null
  const [owner, ts] = job.teamspace.split('/')
  const enc = encodeURIComponent
  return `${cloud.replace(/\/$/, '')}/${enc(owner!)}/${enc(ts!)}/jobs/${enc(job.name)}?app_id=jobs`
}

const esc = (t: string) =>
  t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const rect = (x: number, w: number, fill: string, opacity = 1) =>
  w <= 0
    ? ''
    : `<rect x="${x.toFixed(1)}" y="0" width="${w.toFixed(1)}" height="${BAR_H}" rx="3" fill="${fill}"` +
      (opacity < 1 ? ` fill-opacity="${opacity}"` : '') +
      '/>'

// a stage or run that has started but not reported progress yet: a dashed outline with a slow
// pulse, since a faint full-width fill reads as a full bar on the dark theme
const PULSE = '<animate attributeName="stroke-opacity" values="0.4;1;0.4" dur="2s" repeatCount="indefinite"/>'
const waiting = (x: number, w: number, stroke: string) =>
  w <= 0
    ? ''
    : `<rect x="${(x + 0.5).toFixed(1)}" y="0.5" width="${(w - 1).toFixed(1)}" height="${BAR_H - 1}" rx="3" ` +
      `fill="none" stroke="${stroke}" stroke-width="1" stroke-dasharray="4 3">${PULSE}</rect>`

function fillColor(c: RunCard): string {
  if (c.phase === 'done') return COLOR.done
  if (c.phase === 'stopped') return COLOR.track
  if (['failed', 'waiting', 'abandoned', 'stalled'].includes(c.phase)) return COLOR.bad
  return COLOR.run
}

/** The bar as an SVG document: one segment per stage when the run has them, else one bar with the
 * ground lost to a setback shaded between now and the peak. Each segment carries a hover title. */
export function barSvg(c: RunCard): string {
  const parts: string[] = []
  if (c.stages.length) {
    const gap = 3
    const w = (BAR_W - gap * (c.stages.length - 1)) / c.stages.length
    c.stages.forEach((st, i) => {
      const x = i * (w + gap)
      const took = st.seconds !== null ? ` · ${fmtDuration(st.seconds)}` : ''
      const pct = st.fraction !== null ? ` · ${Math.floor(st.fraction * 100)}%` : ''
      const title = `<title>${esc(`${st.name}${took}${pct}`)}</title>`
      let seg = rect(x, w, COLOR.track, 0.25)
      if (st.state === 'done') seg = rect(x, w, COLOR.done)
      else if (st.state === 'active' && st.fraction !== null) seg += rect(x, w * st.fraction, fillColor(c))
      else if (st.state === 'active') seg = c.isFinal ? rect(x, w, fillColor(c), 0.6) : rect(x, w, COLOR.track, 0.25) + waiting(x, w, fillColor(c))
      parts.push(`<g>${title}${seg}</g>`)
    })
  } else {
    const f = c.fraction ?? 0
    parts.push(rect(0, BAR_W, COLOR.track, 0.25))
    if (c.peak !== null && c.peak > f) parts.push(rect(BAR_W * f, BAR_W * (c.peak - f), COLOR.lost, 0.7))
    if (c.fraction === null && !c.isFinal && c.phase !== 'pending') {
      parts.push(waiting(0, BAR_W, fillColor(c)))
    } else {
      parts.push(rect(0, BAR_W * f, fillColor(c)))
    }
    parts.push(`<title>${esc(`${c.run} · ${c.headline}`)}</title>`)
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${BAR_W}" height="${BAR_H}" ` +
    `viewBox="0 0 ${BAR_W} ${BAR_H}">${parts.join('')}</svg>`
  )
}

/** What the bar says, for a reader that can't see it. */
export function barAlt(c: RunCard): string {
  const stages = c.stages.length
    ? ` Stages: ${c.stages.map(st => `${st.name} ${st.state}`).join(', ')}.`
    : ''
  return `${c.run}: ${c.headline}.${stages}`
}
