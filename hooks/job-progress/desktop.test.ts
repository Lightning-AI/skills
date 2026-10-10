import { describe, expect, test } from 'claude-code/testing'

import { barAlt, barSvg, jobUrl, MAX_CARDS, moreLabel, toCard, toCards, visibleCards } from './desktop'
import type { RunState } from './render'

const NOW = 10_000

const base: RunState = {
  run: 'eval-base',
  phase: 'running',
  step: null,
  total: null,
  epoch: null,
  peak: null,
  peak_epoch: null,
  eta_s: null,
  stage: null,
  stage_since: null,
  stage_index: null,
  stage_count: null,
  stages: [],
  attempt_no: 1,
  setbacks: [],
  lost_s: 0,
  last_error: null,
  last_sample_at: null,
  issue_since: null,
  pending_since: null,
  pending_note: null,
  started_at: NOW - 500,
  finished_at: null,
  updated_at: NOW - 2,
  cost: 0.2,
}

// the eval job from the screenshot: stage 1 of 6 running, no reading yet
const staged: RunState = {
  ...base,
  stage: 'humaneval',
  stage_since: NOW - 176,
  stage_index: 1,
  stage_count: 6,
  stages: [{ name: 'humaneval', start: NOW - 176, end: null, attempt: 1 }],
}

const count = (svg: string, what: string) => svg.split(what).length - 1

describe('cards', () => {
  test('a staged run: one segment per declared stage, the open one pulsing', () => {
    const c = toCard(staged, { name: 'eval-base-1', teamspace: 'me/ts' }, NOW)
    expect(c.stages.map(s => s.state)).toEqual(['active', 'todo', 'todo', 'todo', 'todo', 'todo'])
    expect(c.headline).toBe('0% · humaneval · 2m56s')
    expect(c.detail).toBe('stage 1/6: humaneval · no progress reported yet · 2m56s')
    expect(c.cost).toBe('$0.20')
    const svg = barSvg(c)
    expect(count(svg, '<g>')).toBe(6)
    expect(count(svg, '<animate')).toBe(1)
    expect(svg).toContain('<title>humaneval · 2m56s</title>')
  })

  test('no progress yet is a dashed outline, never a fill that reads as a full bar', () => {
    // the screenshot: g2-s0-replay-half at 0% looked like a full bar on the dark theme
    const run = barSvg(toCard({ ...base, phase: 'starting' }, null, NOW))
    expect(run).toContain('stroke-dasharray')
    expect(run).not.toMatch(/width="480\.0"[^>]*fill="#3b82f6"/)
    const stage = barSvg(toCard(staged, null, NOW))
    expect(stage).toContain('stroke-dasharray')
    expect(stage).not.toContain('fill="#3b82f6"')
  })

  test('a setback shades the ground lost between now and the peak', () => {
    const c = toCard({ ...base, step: 300, total: 1000, peak: 600, eta_s: 900, setbacks: [{}], lost_s: 95 }, null, NOW)
    expect(c.fraction).toBe(0.3)
    expect(c.peak).toBe(0.6)
    expect(c.headline).toBe('30% · ETA 15m00s · ↺1 (+1m35s)')
    expect(barSvg(c)).toContain('#f59e0b')
  })

  test('pending, failed and done runs', () => {
    expect(toCard({ ...base, phase: 'pending', pending_since: NOW - 317 }, null, NOW).headline).toBe(
      'waiting for machine · 5m17s',
    )
    const failed = toCard({ ...base, phase: 'waiting', issue_since: NOW - 60, last_error: 'CUDA OOM' }, null, NOW)
    expect(failed.detail).toBe('CUDA OOM')
    expect(barSvg(failed)).toContain('#ef4444')
    const done = toCard({ ...base, phase: 'done', finished_at: NOW - 5, started_at: NOW - 193 }, null, NOW)
    expect(done.fraction).toBe(1)
    expect(done.headline).toBe('done in 3m08s')
    expect(barSvg(done)).not.toContain('<animate')
  })

  test('another session’s and long-finished runs stay out, live ones come first', () => {
    const old = { ...base, run: 'old', phase: 'done', finished_at: NOW - 3600 }
    const done = { ...base, run: 'setup', phase: 'done', finished_at: NOW - 5 }
    expect(toCards([done, old, staged], {}, NOW).map(c => c.run)).toEqual(['eval-base', 'setup'])
  })

  test('three cards collapsed, every card once expanded', () => {
    const runs = ['a', 'b', 'c', 'd', 'e'].map((run, i) => ({ ...base, run, started_at: NOW - 100 + i }))
    const all = toCards(runs, {}, NOW)
    expect(all.map(c => c.run)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(MAX_CARDS).toBe(3)
    expect(visibleCards(all, false).map(c => c.run)).toEqual(['a', 'b', 'c'])
    expect(visibleCards(all, true)).toHaveLength(5)
    expect(moreLabel(2, false)).toBe('Show 2 more runs')
    expect(moreLabel(1, false)).toBe('Show 1 more run')
    expect(moreLabel(0, true)).toBe('Show fewer runs')
  })

  test('stages without a declared count draw one bar, not a split one', () => {
    // the eval logs from the screenshot: `PROGRESS_PHASE queued` then `PROGRESS_PHASE running`, no
    // count, so an instant `queued` stage filled half the bar green before any progress
    const evals: RunState = {
      ...base,
      stage: 'running',
      stage_since: NOW - 60,
      step: 145,
      total: 581,
      eta_s: 180,
      stages: [
        { name: 'queued', start: NOW - 60, end: NOW - 60, attempt: 1 },
        { name: 'running', start: NOW - 60, end: null, attempt: 1 },
      ],
    }
    const c = toCard(evals, null, NOW)
    expect(c.stages).toEqual([])
    expect(c.fraction).toBe(145 / 581)
    expect(c.headline).toBe('24% · ETA 3m00s')
    const svg = barSvg(c)
    expect(svg).not.toContain('#22c55e')
    expect(svg).toMatch(/width="119\.\d"[^>]*fill="#3b82f6"/)
  })

  test('stage names are escaped in the SVG', () => {
    const c = toCard({ ...staged, stage: 'a<b>&"c"', stages: [{ name: 'a<b>&"c"', start: NOW, end: null }] }, null, NOW)
    expect(barSvg(c)).toContain('a&lt;b&gt;&amp;&quot;c&quot;')
    expect(barAlt(c)).toContain('eval-base')
  })
})

describe('job links', () => {
  test('as the SDK builds them', () => {
    expect(jobUrl({ name: 'sft train', teamspace: 'me/ts' })).toBe('https://lightning.ai/me/ts/jobs/sft%20train?app_id=jobs')
    expect(jobUrl({ name: 'x', teamspace: 'me/ts' }, 'https://staging.lightning.ai/')).toBe(
      'https://staging.lightning.ai/me/ts/jobs/x?app_id=jobs',
    )
    expect(jobUrl({ name: 'x', teamspace: null })).toBeNull()
  })
})
