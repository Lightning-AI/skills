// Live job progress inside Claude Code: the bars above the prompt, a tool that starts the poller
// outside the sandbox, and a message to Claude for each event that needs a reply. It reads the
// files lightning-jobs/progress.py writes (see that skill's "Live progress" section), so it works
// beside the status line and the Monitor, and with any poller that writes the same files.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { RunCard, Watcher } from '../../types'
import { createDelivery } from './delivery'
import { BAR_H, BAR_W, barAlt, barSvg, hiddenCards, jobUrl, moreLine, toCards, type JobRef } from './desktop'
import { FINAL_VISIBLE_FOR, isFinal, isStale, renderAll, type RunState } from './render'

const rows = atom({ plugin: 'lightning', key: 'rows' } as const, [])
const cards = atom({ plugin: 'lightning', key: 'cards' } as const, [])
const moreCards = atom({ plugin: 'lightning', key: 'moreCards' } as const, 0)
const watchers = atom({ plugin: 'lightning', key: 'watchers' } as const, {})

const TOOL = 'watch_job'
const REFRESH_MS = 2000
const BATCH_MS = 1500
const RELAUNCH_WAIT = '1800'
const PYTHONS = [['python3'], ['python'], ['py', '-3']]
// skills whose jobs and Studio runs report progress through progress.py
const PROGRESS_SKILLS = /(^|:)lightning-(jobs|studios)$/
// the poller's notes about the status line, which the mod replaces, so Claude has nothing to do with them
const STATUS_LINE_HINT = /status[- ]line/
// progress the bars already show (job_progress/events.py ROUTINE_KINDS)
const ROUTINE_KINDS = ['milestone', 'stage', 'started', 'recovered', 'watching']
// a poller or event feed started by hand, which the mod does better (see handNote)
const HAND_WATCH = /(progress\.py['"]?\s+watch|lightning\s+job\s+watch)\b/
const HAND_EVENTS = /progress\.py['"]?\s+events\b/
// a line a run prints for the poller (the skill's progress protocol), not the word in prose
const PROGRESS_LINE = /\bPROGRESS\s+\d+\s*\/\s*\d+|\bPROGRESS_PHASE\s+\S|\bPROGRESS_EXIT\s+-?\d/
// a Monitor that picks those lines out of a log
const PROGRESS_FILTER = /\bPROGRESS(_PHASE|_EXIT)?\b/
const NUDGE_EVERY_MS = 10 * 60 * 1000

type RunFile = { session?: string | null; jobs?: { kind?: string; name?: string; teamspace?: string | null }[] }

/** The skill text's addendum: the mod does steps 2-4 of the skill's "Live progress" workflow. */
export function skillNote(hasBar: boolean): string {
  return [
    '',
    '## Live progress in this session',
    '',
    "The lightning plugin's job-progress mod is loaded, so the live-progress workflow is shorter:",
    '',
    `- **Start the poller with the \`mcp__lightning__${TOOL}\` tool**, not Bash: it takes the same`,
    '  arguments as `progress.py watch` and runs it outside the sandbox, so there is nothing to approve.',
    '- **Skip the Monitor and the status-line offer.** The mod sends you a message for each event that',
    '  needs a reply (stalls, setbacks, failures, relaunches, the final state); act on those.',
    hasBar
      ? '- **The bars show above the prompt**, so there is no need to repeat routine progress.'
      : '- **Nothing draws a bar here**, so the mod also sends routine progress: report it in a line.',
    '',
  ].join('\n')
}

/** `watch_job`'s input as `progress.py watch` arguments. */
export function watchArgs(input: Record<string, unknown>): string[] {
  const str = (k: string) => (typeof input[k] === 'string' && input[k] !== '' ? (input[k] as string) : undefined)
  const args = str('job') ? [str('job')!] : []
  for (const k of ['studio', 'log', 'run', 'teamspace', 'note', 'query']) {
    const v = str(k)
    if (v) args.push(`--${k}`, v)
  }
  return [...args, '--relaunch-wait', RELAUNCH_WAIT]
}

/** Lines of a child's stdout, from the pieces it arrives in. */
async function* lines(stream: AsyncIterable<{ stream: string; text: string }>): AsyncGenerator<string> {
  let buf = ''
  for await (const { stream: which, text } of stream) {
    if (which !== 'stdout') continue
    buf += text
    let i: number
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (line) yield line
    }
  }
  if (buf.trim()) yield buf.trim()
}

// children and timers die with the module, so these start over on a reload too
const feeds = new Set<string>()
const pending: string[] = []
let python: string[] | null = null
let sdkWatch: boolean | null = null
// an SDK watcher between its start and its first line, when refresh can't yet tell its run is fed
let sdkStarting = 0
let isFlushing = false
let delivery: ReturnType<typeof createDelivery> | null = null
// this session's runs whose poller is alive, as the last refresh saw them
let liveRuns = 0
let lastNudgeMs = 0
let lastHandMs = 0

async function stateDir($: EngineInterface): Promise<string> {
  const own = await $.env.get('LIGHTNING_PROGRESS_DIR')
  if (own) return own
  const xdg = await $.env.get('XDG_STATE_HOME')
  const home = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE')) ?? ''
  return `${xdg || `${home}/.local/state`}/lightning-progress`
}

async function canDraw($: EngineInterface): Promise<boolean> {
  return (await $.session.surfaces()).some(s => s === 'terminal' || s === 'desktop')
}

/** The first Python on PATH; progress.py finds the SDK's own interpreter itself. */
async function pythonCmd($: EngineInterface): Promise<string[]> {
  if (python) return python
  for (const cmd of PYTHONS) {
    try {
      const { exitCode } = await $.process.run([...cmd, '--version'], { timeoutMs: 10_000 })
      if (exitCode === 0) return (python = cmd)
    } catch {
      // not installed under this name
    }
  }
  throw new Error('no Python found (tried python3, python, py -3)')
}

async function progressCmd($: EngineInterface, args: string[]): Promise<{ argv: string[]; env: Record<string, string> }> {
  const script = `${$.plugin.root}/lightning-jobs/progress.py`
  // tags the run with this session, so the status line and the feed keep to it
  return { argv: [...(await pythonCmd($)), script, ...args], env: { CLAUDE_CODE_SESSION_ID: await $.session.id() } }
}

/** Lines from the feeds go to Claude together, as one note, once they stop coming. */
function tell($: EngineInterface, line: string): void {
  pending.push(line)
  if (isFlushing) return
  isFlushing = true
  $.clock.after(BATCH_MS, async () => {
    isFlushing = false
    const batch = pending.splice(0)
    if (batch.length) await deliveryFor($).deliver(batch)
  })
}

/** The note goes into the running turn as a user-role row Claude reads and the person doesn't see. */
async function appendNote($: EngineInterface, text: string): Promise<boolean> {
  try {
    const out = await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } })
    return !('deny' in out && out.deny)
  } catch (err) {
    $.ui.log(`could not add the note to the running turn: ${String(err)}`, { to: 'debug' })
    return false
  }
}

async function submitNote($: EngineInterface, text: string): Promise<void> {
  await $.prompt.submit({ text })
}

function deliveryFor($: EngineInterface): ReturnType<typeof createDelivery> {
  delivery ??= createDelivery({ append: text => appendNote($, text), submit: text => submitNote($, text) })
  return delivery
}

/** `progress.py events --run RUN`: the Monitor's feed, with its cursor, read by the mod. */
function startFeed($: EngineInterface, run: string): void {
  feeds.add(run)
  void (async () => {
    try {
      const all = !(await canDraw($))
      const { argv, env } = await progressCmd($, ['events', '--run', run, ...(all ? ['--all'] : [])])
      for await (const line of lines($.process.spawn({ argv, env }))) {
        if (!STATUS_LINE_HINT.test(line)) tell($, line)
      }
    } catch (err) {
      $.ui.log(`${run}: event feed stopped: ${String(err)}`, { to: 'debug' })
    } finally {
      feeds.delete(run)
    }
  })()
}

/** Whether the installed `lightning` CLI has `job watch`, which writes the same files. */
async function hasSdkWatch($: EngineInterface): Promise<boolean> {
  if (sdkWatch !== null) return sdkWatch
  try {
    sdkWatch = (await $.process.run(['lightning', 'job', 'watch', '--help'], { timeoutMs: 20_000 })).exitCode === 0
  } catch {
    sdkWatch = false
  }
  return sdkWatch
}

type WatchEvent = { kind?: string; run?: string; msg?: string }

/** The SDK's `--json` lines: events, plus state snapshots the mod reads from the files anyway. */
export function parseWatchLine(line: string): WatchEvent | null {
  try {
    const e = JSON.parse(line) as unknown
    return e && typeof e === 'object' ? (e as WatchEvent) : null
  } catch {
    return null
  }
}

/** Which events Claude hears about: those `progress.py events` passes on, or all where no bar shows. */
export function isWanted(kind: string | undefined, hasBar: boolean): boolean {
  if (!kind || kind === 'state' || kind === 'hint') return false
  return !hasBar || !ROUTINE_KINDS.includes(kind)
}

/** Starts the poller for the session's life; resolves with what it said first. Jobs go to
 * `lightning job watch --json` where the CLI has it, whose events the mod reads straight off its
 * output; Studio logs, and older CLIs, to `progress.py watch`, with `progress.py events` as the feed. */
function startWatch($: EngineInterface, args: string[]): Promise<string> {
  return new Promise(resolve => {
    void (async () => {
      let first: string | null = null
      let run: string | null = null
      let isCounted = false
      const settle = () => {
        if (isCounted) sdkStarting -= 1
        isCounted = false
      }
      const isJob = !args.includes('--studio')
      try {
        const viaSdk = isJob && (await hasSdkWatch($))
        const hasBar = await canDraw($)
        const env = { CLAUDE_CODE_SESSION_ID: await $.session.id() }
        const argv = viaSdk ? ['lightning', 'job', 'watch', ...args, '--json'] : (await progressCmd($, ['watch', ...args])).argv
        if (viaSdk) {
          sdkStarting += 1
          isCounted = true
        }
        const child = $.process.spawn({ argv, env })
        for await (const line of lines(child)) {
          const e = viaSdk ? parseWatchLine(line) : null
          if (first === null) {
            settle()
            first = viaSdk ? (e?.msg ?? line) : line
            resolve(first)
            run = viaSdk ? (e?.kind === 'watching' ? (e.run ?? null) : null) : (/^run (\S+): watching/.exec(line)?.[1] ?? null)
            if (run) {
              // this child is the run's feed; restarted after a reload, it carries on as the same run
              if (viaSdk) feeds.add(run)
              const again = args.includes('--run') ? args : [...args, '--run', run]
              await keepWatchers($, w => ({ ...w, [run!]: { args: again } }))
            }
            continue
          }
          if (e?.msg && isWanted(e.kind, hasBar)) tell($, e.msg)
        }
        const { code } = await child.result
        if (first === null) resolve(`the poller exited with code ${code} before it started; check its arguments`)
      } catch (err) {
        resolve(`could not start the poller: ${String(err)}`)
      } finally {
        settle()
        if (run) {
          feeds.delete(run)
          await keepWatchers($, w => Object.fromEntries(Object.entries(w).filter(([k]) => k !== run)))
        }
      }
    })()
  })
}

/** The session's pollers, kept in its state (a hot reload) and in the store under its id (the
 * session itself restarting, as the desktop app does), so either brings them back. */
async function keepWatchers($: EngineInterface, fn: (w: Record<string, Watcher>) => Record<string, Watcher>): Promise<void> {
  await update($, watchers, fn)
  try {
    const key = `watchers:${await $.session.id()}`
    const now = await read($, watchers)
    if (Object.keys(now).length) await $.store.set(key, now)
    else await $.store.delete(key)
  } catch (err) {
    // the poller runs on regardless; it just won't come back after a restart of the session
    $.ui.log(`could not save the pollers for a restart: ${String(err)}`, { to: 'debug' })
  }
}

async function refresh($: EngineInterface): Promise<void> {
  const dir = await stateDir($)
  const nowMs = await $.clock.now()
  const session = await $.session.id()
  let entries: Awaited<ReturnType<EngineInterface['fs']['list']>> = []
  try {
    entries = await $.fs.list(`${dir}/state`)
  } catch {
    // no poller has run on this machine yet
  }
  const states: RunState[] = []
  const jobs: Record<string, JobRef> = {}
  let live = 0
  for (const f of entries) {
    if (f.kind !== 'file' || !f.name.endsWith('.json') || nowMs - f.mtimeMs > FINAL_VISIBLE_FOR * 1000) continue
    try {
      const s = JSON.parse(await $.fs.read(`${dir}/state/${f.name}`)) as RunState
      const runfile = JSON.parse(await $.fs.read(`${dir}/runs/${s.run}.json`).catch(() => '{}')) as RunFile
      // this session's runs, plus runs started outside Claude Code, as the status line shows
      if (runfile.session && runfile.session !== session) continue
      states.push(s)
      const last = runfile.jobs?.at(-1)
      if (last?.kind === 'job' && last.name) jobs[s.run] = { name: last.name, teamspace: last.teamspace ?? null }
      if (runfile.session === session && !isFinal(s) && !isStale(s, nowMs / 1000)) live += 1
      if (runfile.session === session && !isFinal(s) && !feeds.has(s.run) && !sdkStarting) startFeed($, s.run)
    } catch {
      // a state file mid-write; the next tick reads it
    }
  }
  liveRuns = live
  const now = nowMs / 1000
  const next = renderAll(states, now)
  if (JSON.stringify(next) !== JSON.stringify(await read($, rows))) await update($, rows, () => next)
  const nextCards = toCards(states, jobs, now)
  if (JSON.stringify(nextCards) !== JSON.stringify(await read($, cards))) await update($, cards, () => nextCards)
  const more = hiddenCards(states, now)
  if (more !== (await read($, moreCards))) await update($, moreCards, () => more)
}

/** Stops a run's job after the person confirms, and tells Claude, so it doesn't wait on it. */
async function stopJob($: EngineInterface, card: RunCard): Promise<void> {
  if (!card.job) return
  const { name, teamspace } = card.job
  let answer: string
  try {
    answer = await $.ui.ask(`Stop the Lightning job ${name}? It stops billing, and the run ends as stopped.`, [
      'Stop it',
      'Keep it running',
    ])
  } catch {
    return // dismissed
  }
  if (answer !== 'Stop it') return
  try {
    const argv = ['lightning', 'job', 'stop', name, ...(teamspace ? ['--teamspace', teamspace] : [])]
    const { exitCode, stderr } = await $.process.run(argv, { timeoutMs: 60_000 })
    if (exitCode !== 0) throw new Error(stderr.trim().split('\n').at(-1) || `exit code ${exitCode}`)
    $.ui.toast(`Stopped ${name}`)
    tell($, `${card.run}: the user stopped job ${name} from the progress band`)
  } catch (err) {
    $.ui.toast(`Could not stop ${name}: ${String(err).slice(0, 120)}`, { timeoutMs: 8000 })
  }
}

/** What Claude reads about a command that starts a poller or an event feed by hand, which the mod
 * does itself, or null. A hand-started poller needs the sandbox lifted and breaks on shell
 * quoting; a Monitor on the events repeats the notes the mod already sends. The skill's own note
 * says this, but only where its text reaches Claude. A Bash command only reminds, since the text
 * can be a script or a file being written; a Monitor on the events is refused. */
export function handNote(command: string): string | null {
  if (HAND_EVENTS.test(command)) {
    return (
      "No Monitor needed: the lightning plugin's job-progress mod already follows this run's events and sends " +
      `you each one that needs a reply. Start pollers with \`mcp__lightning__${TOOL}\`.`
    )
  }
  if (HAND_WATCH.test(command)) {
    return (
      `The lightning plugin's job-progress mod is loaded: start pollers with the \`mcp__lightning__${TOOL}\` ` +
      'tool (load it with ToolSearch `select:mcp__lightning__watch_job` if needed), not Bash. It takes the same ' +
      'arguments as `progress.py watch` (job, or studio and log; run, teamspace, note), runs outside the sandbox, ' +
      'draws the bars and sends you the events that need a reply. One call per job; skip the Monitor.'
    )
  }
  return null
}

/** Whether a Bash call's output, or a Monitor's command, shows a run reporting progress with no
 * poller. A Bash command that only mentions the word (a commit message, a grep) is not one. */
export const reportsProgress = (command: string, output: string, isMonitor = false): boolean =>
  !HAND_WATCH.test(command) &&
  !HAND_EVENTS.test(command) &&
  (PROGRESS_LINE.test(output) || (isMonitor && PROGRESS_FILTER.test(command)))

export const NUDGE =
  'This run reports PROGRESS lines, but no progress poller is watching any run of this session, so the ' +
  `person sees no bar. Start one now with \`mcp__lightning__${TOOL}\` (a job name, or studio and log), ` +
  'one per job or log, rather than following the log yourself.'

/** The reminders for a Bash or Monitor call: a poller started by hand, or a run with none. Each at
 * most every NUDGE_EVERY_MS. */
async function nudgesFor($: EngineInterface, command: string, output: string, isMonitor = false): Promise<string[]> {
  const out: string[] = []
  const now = await $.clock.now().catch(() => 0)
  if (!now) return out
  const hand = handNote(command)
  if (hand && now - lastHandMs >= NUDGE_EVERY_MS) {
    lastHandMs = now
    out.push(hand)
  }
  if (!liveRuns && reportsProgress(command, output, isMonitor) && now - lastNudgeMs >= NUDGE_EVERY_MS) {
    lastNudgeMs = now
    out.push(NUDGE)
  }
  return out
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.tool.register({
      name: TOOL,
      description:
        'Start the lightning-jobs live-progress poller (`lightning job watch`, or `progress.py watch` where the ' +
        'CLI lacks it) for a Lightning job, or for a ' +
        'log file in a Studio, outside the agent sandbox. The progress bars then show above the prompt and ' +
        'you get a message for each event that needs a reply. Returns the run name the poller tracks.',
      inputSchema: {
        type: 'object',
        properties: {
          job: { type: 'string', description: 'job name (or use studio and log)' },
          studio: { type: 'string', description: 'Studio whose log file to follow instead of a job' },
          log: { type: 'string', description: 'log file inside the Studio, ending with PROGRESS_EXIT <code>' },
          run: { type: 'string', description: 'run this belongs to, e.g. to relaunch a failed run into it' },
          teamspace: { type: 'string', description: 'owner/teamspace (default: the CLI configured one)' },
          note: { type: 'string', description: 'why this job was (re)launched' },
          query: { type: 'string', description: 'server-side log filter, e.g. PROGRESS for very chatty jobs' },
        },
      },
    })
    // a reload or a restart of the session killed the pollers this mod started; start them again
    const stored = ((await $.store.get(`watchers:${await $.session.id()}`).catch(() => undefined)) ?? {}) as Record<string, Watcher>
    for (const w of Object.values({ ...stored, ...(await read($, watchers)) })) void startWatch($, w.args)
    $.clock.every(REFRESH_MS, () => refresh($))
    void refresh($)
    return started
  })

  on('tool.call', { tool: `mcp__lightning__${TOOL}` }, async ($, e) => {
    const args = watchArgs(e as unknown as Record<string, unknown>)
    if (!args[0] || args[0].startsWith('--')) {
      if (!(typeof e.studio === 'string' && typeof e.log === 'string')) {
        return { deny: 'Give a job name, or both studio and log.' }
      }
    }
    const first = await startWatch($, args)
    void refresh($)
    return { result: first }
  })

  // a step that calls a tool is followed by another request, which reads the notes held for it
  on('tool.call', async ($, e, next) => {
    // a failure here must not hold up the tool
    if (!e.agentId) await deliveryFor($).toolStarted().catch(err => $.ui.log(`could not pass on the held notes: ${String(err)}`, { to: 'debug' }))
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const out = await next(e)
    if ('deny' in out && out.deny) return out
    const nudges = await nudgesFor($, e.command, out.text ?? '')
    return nudges.length ? { ...out, context: [...(out.context ?? []), ...nudges] } : out
  })

  on('tool.call', { tool: 'Monitor' }, async ($, e, next) => {
    if (e.command && HAND_EVENTS.test(e.command)) return { deny: handNote(e.command)! }
    const out = await next(e)
    if (!e.command || ('deny' in out && out.deny)) return out
    const nudges = await nudgesFor($, e.command, '', true)
    return nudges.length ? { ...out, context: [...(out.context ?? []), ...nudges] } : out
  })

  // notes appended mid-turn reach Claude with the turn's next request; see delivery.ts
  on('turn.start', async ($, e, next) => {
    deliveryFor($).turnStarted()
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    if (!e.agentId) deliveryFor($).stepStarted()
    return yield* next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const out = await next(e)
    if (!e.agentId) for (const line of deliveryFor($).turnEnded()) tell($, line)
    return out
  })

  on('skill.prompt', async ($, e, next) => {
    const out = await next(e)
    if (!PROGRESS_SKILLS.test(e.skill)) return out
    return { text: out.text + skillNote(await canDraw($)) }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    if (e.surface === 'desktop') {
      const list = await read($, cards)
      if (!list.length) return next(e)
      const more = await read($, moreCards)
      const cloud = (await $.env.get('LIGHTNING_CLOUD_URL')) || undefined
      const { Box, Button, Link, Svg, Text } = $.ui.resolve(e)
      return (
        <Box flexDirection="column" gap={1}>
          {list.map(c => {
            const url = c.job ? jobUrl(c.job, cloud) : null
            return (
              <Box key={`run:${c.run}`} flexDirection="column">
                <Box flexDirection="row" gap={1}>
                  <Text bold dimColor={c.isStale}>
                    {c.icon} {c.run}
                  </Text>
                  <Text dimColor wrap="truncate">
                    {c.headline}
                    {c.cost ? ` · ${c.cost}` : ''}
                  </Text>
                </Box>
                <Svg source={barSvg(c)} alt={barAlt(c)} width={BAR_W} height={BAR_H} />
                {c.detail ? (
                  <Text dimColor wrap="truncate">
                    {c.detail}
                  </Text>
                ) : null}
                {url || (c.job && !c.isFinal) ? (
                  <Box flexDirection="row" gap={2}>
                    {url ? <Link href={url} label="Open in Lightning" /> : null}
                    {c.job && !c.isFinal ? (
                      <Button key={`stop:${c.run}`} label="Stop job" onPress={() => void stopJob($, c)} />
                    ) : null}
                  </Box>
                ) : null}
              </Box>
            )
          })}
          {more ? <Text dimColor>{moreLine(more)}</Text> : null}
        </Box>
      )
    }
    const list = await read($, rows)
    if (!list.length) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {list.slice(0, e.props.maxRows).map(r => (
          <Text dimColor={r.isStale} wrap="truncate">
            {r.text}
          </Text>
        ))}
      </Box>
    )
  })
}
