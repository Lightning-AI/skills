import { describe, expect, mock, test } from 'claude-code/testing'

import { isWanted, parseWatchLine, skillNote, watchArgs } from './register'

const DIR = '/state/lightning-progress'
const NOW_MS = 10_000_000

const STATE = {
  run: 'train-run',
  phase: 'running',
  step: 450,
  total: 1000,
  epoch: null,
  peak: null,
  peak_epoch: null,
  eta_s: 725,
  stage: null,
  stage_since: null,
  stage_index: null,
  stage_count: null,
  stages: [],
  attempt_no: 1,
  setbacks: [],
  lost_s: 0,
  last_error: null,
  last_sample_at: NOW_MS / 1000 - 3,
  issue_since: null,
  pending_since: null,
  pending_note: null,
  started_at: NOW_MS / 1000 - 600,
  finished_at: null,
  updated_at: NOW_MS / 1000 - 2,
  cost: null,
}

const FILES: Record<string, string> = {
  [`${DIR}/state/train-run.json`]: JSON.stringify(STATE),
  // another session's run stays out of this session's band
  [`${DIR}/state/theirs.json`]: JSON.stringify({ ...STATE, run: 'theirs' }),
  [`${DIR}/runs/theirs.json`]: JSON.stringify({ session: 'someone-else' }),
}

describe('watch_job arguments', () => {
  test('a job, with options in progress.py watch flags', () => {
    expect(watchArgs({ job: 'train-run-42', teamspace: 'me/ts', note: '' })).toEqual([
      'train-run-42',
      '--teamspace',
      'me/ts',
      '--relaunch-wait',
      '1800',
    ])
  })

  test('a Studio log', () => {
    expect(watchArgs({ studio: 'dev', log: 'train.log' })).toEqual(['--studio', 'dev', '--log', 'train.log', '--relaunch-wait', '1800'])
  })
})

describe('skill note', () => {
  test('points at the tool and drops the Monitor', () => {
    const note = skillNote(true)
    expect(note).toContain('mcp__lightning__watch_job')
    expect(note).toContain('Skip the Monitor')
    expect(skillNote(false)).toContain('Nothing draws a bar here')
  })

  test('is added to the jobs and studios skills only', async ($, on) => {
    on('session.surfaces', async () => ({ value: ['terminal'] }))
    on('skill.prompt', async (_$, e) => ({ text: e.text }))
    expect((await $.skill.prompt({ skill: 'lightning:lightning-jobs', text: 'base' })).text).toContain('watch_job')
    expect((await $.skill.prompt({ skill: 'lightning:lightning-studios', text: 'base' })).text).toContain('watch_job')
    expect((await $.skill.prompt({ skill: 'lightning:lightning-deployments', text: 'base' })).text).toBe('base')
  })
})

test('the band shows this session’s runs on the terminal and the desktop app', async ($, on) => {
  const clock = mock.clock(on, { now: NOW_MS })
  mock.env(on, { LIGHTNING_PROGRESS_DIR: DIR })
  on('session.id', async () => ({ value: 'this-session' }))
  on('session.surfaces', async () => ({ value: ['terminal'] }))
  on('tool.register', async (_$, e) => ({ value: { tool: `mcp__lightning__${e.name}` } }))
  on('fs.list', async () => ({
    value: Object.keys(FILES)
      .filter(p => p.startsWith(`${DIR}/state/`))
      .map(p => ({ name: p.split('/').pop()!, kind: 'file' as const, size: 1, mtimeMs: NOW_MS - 2000, isLink: false })),
  }))
  on('fs.read', async (_$, e) => {
    const text = FILES[e.path]
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: text }
  })

  on('session.start', async (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await clock.settle()

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'lightning', surface, component: 'AbovePrompt', props: {} as never })
    expect(await ui.find({ type: 'Text', text: /train-run .*45%.*ETA 12m05s/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /theirs/ })).toBeUndefined()
    await ui.unmount()
  }
})

describe('lightning job watch', () => {
  test('which events Claude hears about', () => {
    expect(parseWatchLine('{"kind":"retry","run":"r","msg":"r: retry"}')?.kind).toBe('retry')
    expect(parseWatchLine('not json')).toBeNull()
    expect(isWanted('retry', true)).toBe(true)
    expect(isWanted('milestone', true)).toBe(false)
    expect(isWanted('milestone', false)).toBe(true)
    expect(isWanted('state', false)).toBe(false)
    expect(isWanted('hint', false)).toBe(false)
  })

  test('watch_job runs the CLI and passes on the events that need a reply', async ($, on) => {
    const clock = mock.clock(on, { now: NOW_MS })
    mock.env(on, { LIGHTNING_PROGRESS_DIR: DIR })
    const sent: string[] = []
    let spawned: readonly string[] = []
    on('session.id', async () => ({ value: 'this-session' }))
    on('session.surfaces', async () => ({ value: ['terminal'] }))
    on('process.run', async (_$, e) => ({ value: { exitCode: e.argv[0] === 'lightning' ? 0 : 1, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
    on('process.spawn', async function* (_$, e) {
      spawned = e.argv
      const ev = (kind: string, msg: string) => ({ stream: 'stdout' as const, text: JSON.stringify({ kind, run: 'r', msg: `r: ${msg}` }) + '\n' })
      yield ev('watching', 'watching train-run')
      yield { stream: 'stdout' as const, text: '{"kind":"state","run":"r","state":{}}\n' }
      yield ev('milestone', '50%')
      yield ev('retry', 'attempt 2 after OOM')
      yield ev('failed', 'failed')
      return { value: { code: 1, signal: null } }
    })
    on('prompt.submit', async (_$, e) => {
      sent.push(e.text)
      return { value: undefined } as never
    })

    const out = await $.tool.call({ tool: 'mcp__lightning__watch_job', job: 'train-run' } as never)
    expect((out as { result?: unknown }).result).toBe('r: watching train-run')
    expect(spawned.slice(0, 4)).toEqual(['lightning', 'job', 'watch', 'train-run'])
    expect(spawned).toContain('--json')
    await clock.advance(2000)
    expect(sent).toHaveLength(1)
    expect(sent[0]).toContain('r: attempt 2 after OOM')
    expect(sent[0]).toContain('r: failed')
    expect(sent[0]).not.toContain('50%')
  })
})
