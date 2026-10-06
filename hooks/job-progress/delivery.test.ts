import { describe, expect, test } from 'claude-code/testing'

import { createDelivery } from './delivery'

function fixture(canAppend = true) {
  const appended: string[] = []
  const submitted: string[] = []
  const d = createDelivery({
    append: async text => {
      if (canAppend) appended.push(text)
      return canAppend
    },
    submit: async text => {
      submitted.push(text)
    },
  })
  return { d, appended, submitted }
}

describe('delivery', () => {
  test('idle: the note starts a turn', async () => {
    const { d, appended, submitted } = fixture()
    expect(await d.deliver(['r: failed'])).toBe('submitted')
    expect(appended).toHaveLength(0)
    expect(submitted).toEqual(['Lightning job progress:\n- r: failed'])
  })

  test('mid-turn: the note joins the running turn, and a later request reads it', async () => {
    const { d, submitted } = fixture()
    d.turnStarted()
    expect(await d.deliver(['r: done in 3m08s'])).toBe('appended')
    d.stepStarted()
    expect(d.turnEnded()).toEqual([])
    expect(submitted).toHaveLength(0)
  })

  test('while Claude writes a reply with no tool call, the note waits for the turn to end', async () => {
    // the session dump's duplicates: appended there, the note stayed in the conversation and went again
    const { d, appended, submitted } = fixture()
    d.turnStarted()
    d.stepStarted()
    expect(await d.deliver(['r: stalled 6m'])).toBe('held')
    expect(d.turnEnded()).toEqual(['r: stalled 6m'])
    expect(await d.deliver(['r: stalled 6m'])).toBe('submitted')
    expect(appended).toHaveLength(0)
    expect(submitted).toHaveLength(1)
  })

  test('a held note joins the turn at the step’s first tool call', async () => {
    const { d, appended } = fixture()
    d.turnStarted()
    d.stepStarted()
    await d.deliver(['r: failed'])
    await d.toolStarted()
    expect(appended).toEqual(['Lightning job progress:\n- r: failed'])
    // once a tool ran, later notes go straight in: another request follows
    expect(await d.deliver(['r: retry'])).toBe('appended')
    d.stepStarted()
    expect(d.turnEnded()).toEqual([])
  })

  test('a held note whose append is refused still reaches Claude', async () => {
    const { d, submitted } = fixture(false)
    d.turnStarted()
    d.stepStarted()
    await d.deliver(['r: failed'])
    await d.toolStarted()
    expect(d.turnEnded()).toEqual(['r: failed'])
    expect(submitted).toHaveLength(0)
  })

  test('mid-turn, a refused append still reaches Claude', async () => {
    const { d, submitted } = fixture(false)
    d.turnStarted()
    expect(await d.deliver(['r: failed'])).toBe('submitted')
    expect(submitted).toHaveLength(1)
    expect(d.turnEnded()).toEqual([])
  })
})
