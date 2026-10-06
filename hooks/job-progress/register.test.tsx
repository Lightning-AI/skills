import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { handNote, isWanted, NUDGE, parseWatchLine, reportsProgress, skillNote, watchArgs } from './register'

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
  [`${DIR}/runs/train-run.json`]: JSON.stringify({
    session: 'this-session',
    jobs: [{ kind: 'job', name: 'train-run', teamspace: 'me/ts' }],
  }),
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

/** A session with the state files above, started, its first refresh done. */
async function started($: Engine, on: On, store: Record<string, unknown> = {}) {
  const clock = mock.clock(on, { now: NOW_MS })
  mock.env(on, { LIGHTNING_PROGRESS_DIR: DIR })
  mock.store(on, store)
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
  return clock
}

describe('the band', () => {
  test('the terminal draws this session’s runs as ASCII rows', async ($, on) => {
    await started($, on)
    const ui = await $.ui.mount({ plugin: 'lightning', surface: 'terminal', component: 'AbovePrompt', props: {} as never })
    expect(await ui.find({ type: 'Text', text: /train-run .*45%.*ETA 12m05s/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /theirs/ })).toBeUndefined()
    expect(await ui.find({ type: 'Button' })).toBeUndefined()
    await ui.unmount()
  })

  test('the desktop app draws an SVG bar, a link to the job and a stop button', async ($, on) => {
    await started($, on)
    const ui = await $.ui.mount({ plugin: 'lightning', surface: 'desktop', component: 'AbovePrompt', props: {} as never })
    expect(await ui.find({ type: 'Text', text: /45% · ETA 12m05s/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /theirs/ })).toBeUndefined()
    const svg = await ui.find({ type: 'Svg' })
    expect(String(svg?.props.source)).toContain('<svg')
    expect(String(svg?.props.alt)).toContain('train-run')
    // sized to the bar: a frame left to its default height draws a tall empty box
    expect(svg?.props.height).toBe(12)
    expect((await ui.find({ type: 'Link' }))?.props.href).toBe('https://lightning.ai/me/ts/jobs/train-run?app_id=jobs')
    expect(await ui.find({ key: 'stop:train-run' })).toBeDefined()
    await ui.unmount()
  })

  test('a restarted session starts its pollers again', async ($, on) => {
    const spawned: string[][] = []
    on('process.run', async () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
    on('process.spawn', async function* (_$, e) {
      spawned.push([...e.argv])
      return { value: { code: 0, signal: null } }
    })
    await started($, on, { 'watchers:this-session': { 'train-run': { args: ['train-run', '--run', 'train-run'] } } })
    expect(spawned.some(a => a.join(' ').includes('watch train-run --run train-run'))).toBe(true)
  })

  test('stop asks first, then stops the job and tells Claude', async ($, on) => {
    const ran: string[][] = []
    const sent: string[] = []
    const toasts: string[] = []
    let answer = 'Keep it running'
    // $.ui.ask is the AskUserQuestion tool's call; answer it as the person picking `answer`
    on('tool.call', { tool: 'AskUserQuestion' }, async (_$, e) => {
      const q = e.questions[0]!
      return { result: { questions: e.questions, answers: { [q.question]: answer } } } as never
    })
    on('ui.toast', async (_$, e) => {
      toasts.push(String((e as { text?: unknown }).text))
      return { value: undefined } as never
    })
    on('process.run', async (_$, e) => {
      ran.push([...e.argv])
      return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    })
    on('prompt.submit', async (_$, e) => {
      sent.push(e.text)
      return { text: e.text } as never
    })
    const clock = await started($, on)
    const ui = await $.ui.mount({ plugin: 'lightning', surface: 'desktop', component: 'AbovePrompt', props: {} as never })
    await ui.press({ key: 'stop:train-run' })
    expect(ran.filter(a => a[0] === 'lightning')).toHaveLength(0)

    answer = 'Stop it'
    await ui.press({ key: 'stop:train-run' })
    expect(ran.find(a => a[0] === 'lightning')).toEqual(['lightning', 'job', 'stop', 'train-run', '--teamspace', 'me/ts'])
    expect(toasts.join()).toContain('Stopped train-run')
    await clock.advance(2000)
    expect(sent.join('\n')).toContain('the user stopped job train-run')
    await ui.unmount()
  })
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
      return { text: e.text } as never
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

test('an event reaches an idle Claude as a turn of its own', async ($, on) => {
  const clock = mock.clock(on, { now: NOW_MS })
  mock.env(on, { LIGHTNING_PROGRESS_DIR: DIR })
  const sent: string[] = []
  on('session.id', async () => ({ value: 'this-session' }))
  on('session.surfaces', async () => ({ value: ['terminal'] }))
  on('process.run', async () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('process.spawn', async function* () {
    const ev = (kind: string, msg: string) => ({ stream: 'stdout' as const, text: JSON.stringify({ kind, run: 'r', msg: `r: ${msg}` }) + '\n' })
    yield ev('watching', 'watching train-run')
    yield ev('retry', 'attempt 2 after OOM')
    return { value: { code: 0, signal: null } }
  })
  on('prompt.submit', async (_$, e) => {
    sent.push(e.text)
    return { text: e.text } as never
  })
  await $.tool.call({ tool: 'mcp__lightning__watch_job', job: 'train-run' } as never)
  await clock.advance(2000)
  expect(sent).toEqual(['Lightning job progress:\n- r: attempt 2 after OOM'])
})

describe('pollers started by hand', () => {
  test('a poller or event feed started by hand points Claude at watch_job', () => {
    // the session dump: Bash pollers that broke on quoting, and Monitors that repeated the notes
    const py = 'python3 /x/lightning-jobs/progress.py'
    expect(handNote(`export DEBUG=0; ${py} watch --studio g0 --log recipe/g1.log`)).toContain('mcp__lightning__watch_job')
    expect(handNote('lightning job watch g2-s0 --json')).toContain('mcp__lightning__watch_job')
    expect(handNote(`sleep 20; ${py} events --run g1 --all`)).toContain('No Monitor needed')
    expect(handNote(`${py} statusline`)).toBeNull()
    expect(handNote(`${py} abandon g1`)).toBeNull()
  })

  test('progress lines with no poller are spotted, the bare word is not', () => {
    expect(reportsProgress('tail -f out | grep PROGRESS_EXIT', '', true)).toBe(true)
    expect(reportsProgress('tail -n 15 smoke.log', '09:34:05 PROGRESS 10/40')).toBe(true)
    expect(reportsProgress('tail -n 15 smoke.log', '09:30:55 PROGRESS_PHASE smoke_train')).toBe(true)
    expect(reportsProgress('ls', 'nothing')).toBe(false)
    expect(reportsProgress('python3 progress.py watch --studio s --log PROGRESS.log', '', true)).toBe(false)
    // a commit message naming the protocol: this fired on the PR's own commit
    expect(reportsProgress('git commit -m "remind on PROGRESS lines"', '[fix e8b2765] make the bars reliable')).toBe(false)
  })

  test('Bash: a hand-started poller runs, with a reminder; a Monitor on the events is refused', async ($, on) => {
    on('tool.call', { tool: 'Bash' }, async () => ({ result: 'PROGRESS 10/40', text: 'PROGRESS 10/40' }) as never)
    on('tool.call', { tool: 'Monitor' }, async () => ({ result: 'started', text: 'started' }) as never)
    await started($, on)
    // the text can be a script or file being written, so Bash is never refused
    const ran = (await $.tool.call({ tool: 'Bash', command: 'python3 progress.py watch train-run' } as never)) as {
      deny?: string
      context?: string[]
    }
    expect(ran.deny).toBeUndefined()
    expect(ran.context?.[0]).toContain('watch_job')
    // this session's run has a fresh state file, so it counts as watched: no reminder
    const out = await $.tool.call({ tool: 'Bash', command: 'tail -n 5 smoke.log' } as never)
    expect((out as { context?: string[] }).context ?? []).not.toContain(NUDGE)
    const mon = await $.tool.call({ tool: 'Monitor', command: 'python3 progress.py events --run r', description: 'x', timeout_ms: 1 } as never)
    expect((mon as { deny?: string }).deny).toContain('No Monitor needed')
  })
})


test('a run that prints PROGRESS with no poller gets a reminder, at most every 10 minutes', async ($, on) => {
  // the session dump: G2 ran for hours, followed by Claude's own log tails, with no bar
  on('tool.call', { tool: 'Bash' }, async () => ({ result: 'x', text: '09:34:05 PROGRESS 10/40' }) as never)
  const clock = mock.clock(on, { now: NOW_MS })
  mock.env(on, { LIGHTNING_PROGRESS_DIR: DIR })
  mock.store(on, {})
  on('session.id', async () => ({ value: 'this-session' }))
  on('session.surfaces', async () => ({ value: ['desktop'] }))
  on('tool.register', async (_$, e) => ({ value: { tool: `mcp__lightning__${e.name}` } }))
  on('fs.list', async () => ({ value: [] }))
  on('session.start', async (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  await clock.settle()
  const tail = () => $.tool.call({ tool: 'Bash', command: 'tail -n 15 smoke.out' } as never) as Promise<{ context?: string[] }>
  expect((await tail()).context).toEqual([NUDGE])
  expect((await tail()).context ?? []).toEqual([])
  await clock.advance(10 * 60 * 1000)
  expect((await tail()).context).toEqual([NUDGE])
})
