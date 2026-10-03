// The job-progress mod's state, which the host keeps across hot reloads.

/** One line of the band above the prompt, as the terminal draws it. */
export type BandRow = { text: string; isStale: boolean }

/** A poller the mod started: enough to start it again after a reload killed it. */
export type Watcher = { args: string[] }

/** One segment of a run's stage timeline on the desktop app. */
export type CardStage = {
  name: string
  state: 'done' | 'active' | 'todo'
  /** How long the stage took, or has run so far; null for one not started. */
  seconds: number | null
  /** The active stage's own progress, 0-1; null before its first reading. */
  fraction: number | null
}

/** One run as the desktop app draws it: the facts the bar and its buttons need. */
export type RunCard = {
  run: string
  icon: string
  phase: string
  isFinal: boolean
  isStale: boolean
  /** The whole run's progress, 0-1; null when nothing has reported a total yet. */
  fraction: number | null
  /** The furthest the run got before a setback, 0-1; null without one. */
  peak: number | null
  /** The stage timeline; empty for a run that declares no stages. */
  stages: CardStage[]
  /** What the run is doing, e.g. `45% · ETA 12m05s` or `waiting for machine · 5m17s`. */
  headline: string
  /** A second line: the current stage, or why it stalled or failed. */
  detail: string | null
  cost: string | null
  /** The Lightning job behind the run, for its buttons; null for a Studio log. */
  job: { name: string; teamspace: string | null } | null
}

declare module 'claude-code' {
  interface PluginState {
    lightning: {
      rows: BandRow[]
      cards: RunCard[]
      watchers: Record<string, Watcher>
    }
  }
}
