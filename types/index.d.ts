// The job-progress mod's state, which the host keeps across hot reloads.

/** One line of the band above the prompt. */
export type BandRow = { text: string; isStale: boolean }

/** A poller the mod started: enough to start it again after a reload killed it. */
export type Watcher = { args: string[] }

declare module 'claude-code' {
  interface PluginState {
    lightning: {
      rows: BandRow[]
      watchers: Record<string, Watcher>
    }
  }
}
