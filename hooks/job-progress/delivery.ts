// How a note about job progress reaches Claude. Mid-turn it joins the running turn, which reads it
// with its next model request: a prompt submitted then would wait for the turn to end and arrive
// stale, starting a turn of its own just to say it knew. Idle, the note starts a turn, since
// something needs a reply.
//
// A note added while Claude writes a reply that calls no tool is never read in that turn, yet it
// stays in the conversation, so sending it again when the turn ends would repeat it. So a note
// joins the turn only when another request is sure to follow: before the turn's first request, or
// once the current step has called a tool. Otherwise it waits, and goes in at the step's first
// tool call, or as a turn of its own when the turn ends.

export type DeliveryIO = {
  /** Adds the note to the running turn; false when that was refused or failed. */
  append: (text: string) => Promise<boolean>
  /** Starts a turn with the note. */
  submit: (text: string) => Promise<void>
}

export const NOTE_HEAD = 'Lightning job progress:'

export const noteText = (lines: string[]): string => [NOTE_HEAD, ...lines.map(l => `- ${l}`)].join('\n')

export function createDelivery(io: DeliveryIO) {
  let isBusy = false
  // another request is sure to follow: none has started yet, or the current step called a tool
  let isFollowed = false
  let held: string[] = []
  let unseen: string[] = []

  async function append(lines: string[]): Promise<boolean> {
    if (!(await io.append(noteText(lines)))) return false
    unseen.push(...lines)
    return true
  }

  return {
    turnStarted(): void {
      isBusy = true
      isFollowed = true
    },
    /** A model request of the main loop began: it carries every note appended before it. */
    stepStarted(): void {
      unseen = []
      isFollowed = false
    },
    /** The main loop's step called a tool, so another request follows: held notes go in now. */
    async toolStarted(): Promise<void> {
      isFollowed = true
      if (!isBusy || !held.length) return
      const lines = held.splice(0)
      if (!(await append(lines))) held.unshift(...lines)
    },
    /** The turn ended; returns the lines it never read, to deliver again. */
    turnEnded(): string[] {
      isBusy = false
      isFollowed = false
      return [...unseen.splice(0), ...held.splice(0)]
    },
    async deliver(lines: string[]): Promise<'appended' | 'held' | 'submitted'> {
      if (isBusy && !isFollowed) {
        held.push(...lines)
        return 'held'
      }
      if (isBusy && (await append(lines))) return 'appended'
      await io.submit(noteText(lines))
      return 'submitted'
    },
  }
}
