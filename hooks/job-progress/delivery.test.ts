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

  test('a turn that ends before reading the note hands it back', async () => {
    const { d } = fixture()
    d.turnStarted()
    d.stepStarted()
    await d.deliver(['r: stalled 6m'])
    expect(d.turnEnded()).toEqual(['r: stalled 6m'])
    expect(await d.deliver(['r: stalled 6m'])).toBe('submitted')
  })

  test('mid-turn, a refused append still reaches Claude', async () => {
    const { d, submitted } = fixture(false)
    d.turnStarted()
    expect(await d.deliver(['r: failed'])).toBe('submitted')
    expect(submitted).toHaveLength(1)
    expect(d.turnEnded()).toEqual([])
  })
})
