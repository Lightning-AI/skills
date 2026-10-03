// How a note about job progress reaches Claude. Mid-turn it joins the running turn, which reads it
// with its next model request: a prompt submitted then would wait for the turn to end and arrive
// stale, starting a turn of its own just to say it knew. Idle, the note starts a turn, since
// something needs a reply. A turn that ends without another request never read its notes, so
// those go again.

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
  let unseen: string[] = []
  return {
    turnStarted(): void {
      isBusy = true
    },
    /** A model request of the main loop began: it carries every note appended before it. */
    stepStarted(): void {
      unseen = []
    },
    /** The turn ended; returns the lines it never read, to deliver again. */
    turnEnded(): string[] {
      isBusy = false
      return unseen.splice(0)
    },
    async deliver(lines: string[]): Promise<'appended' | 'submitted'> {
      const text = noteText(lines)
      if (isBusy && (await io.append(text))) {
        unseen.push(...lines)
        return 'appended'
      }
      await io.submit(text)
      return 'submitted'
    },
  }
}
