// Parity with lightning-jobs/job_progress/statusline.py: the expected text below is what the Python
// status line printed for the same states (ANSI dimming dropped; the band dims a stale row itself).

import { describe, expect, test } from 'claude-code/testing'

import { bar, fmtDuration, renderAll, renderLine, renderRows, type RunState } from './render'

const NOW = 10_000

const CASES: Record<string, { state: RunState; line: string; rows: string[] }> = {
  "running": {
    "state": {
      "run": "train-run",
      "phase": "running",
      "step": 450,
      "total": 1000,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": 725,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": null,
      "last_sample_at": 9997.0,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": 9400.0,
      "finished_at": null,
      "updated_at": 9998.0,
      "cost": 1.234
    },
    "line": "▶ train-run  ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░   45%  ETA 12m05s · $1.23",
    "rows": [
      "▶ train-run  ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░   45%  ETA 12m05s · $1.23"
    ]
  },
  "recovering": {
    "state": {
      "run": "train-run",
      "phase": "recovering",
      "step": 300,
      "total": 1000,
      "epoch": null,
      "peak": 600,
      "peak_epoch": null,
      "eta_s": 900,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [
        {}
      ],
      "lost_s": 95,
      "last_error": null,
      "last_sample_at": null,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": 9100.0,
      "finished_at": null,
      "updated_at": 9998.0,
      "cost": null
    },
    "line": "⟳ train-run  ▓▓▓▓▓▓▒▒▒▒▒▒░░░░░░░░   30% ↺1  ETA 15m00s (+1m35s) · peak 60%",
    "rows": [
      "⟳ train-run  ▓▓▓▓▓▓▒▒▒▒▒▒░░░░░░░░   30% ↺1  ETA 15m00s (+1m35s) · peak 60%"
    ]
  },
  "pending": {
    "state": {
      "run": "train-run",
      "phase": "pending",
      "step": null,
      "total": null,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": null,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": null,
      "last_sample_at": null,
      "issue_since": null,
      "pending_since": 9958.0,
      "pending_note": null,
      "started_at": null,
      "finished_at": null,
      "updated_at": 9998.0,
      "cost": null
    },
    "line": "⏳ train-run  waiting for machine · 42s",
    "rows": [
      "⏳ train-run  waiting for machine · 42s"
    ]
  },
  "stalled": {
    "state": {
      "run": "train-run",
      "phase": "stalled",
      "step": 10,
      "total": 100,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": null,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": "CUDA out of memory. Tried to allocate 2.00 GiB",
      "last_sample_at": 9600.0,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": null,
      "finished_at": null,
      "updated_at": 9999.0,
      "cost": null
    },
    "line": "⚠ train-run  ▓▓░░░░░░░░░░░░░░░░░░   10%  stalled 6m40s · CUDA out of memory. Tried to allocate 2.00 GiB",
    "rows": [
      "⚠ train-run  ▓▓░░░░░░░░░░░░░░░░░░   10%  stalled 6m40s · CUDA out of memory. Tried to allocate 2.00 GiB"
    ]
  },
  "done": {
    "state": {
      "run": "train-run",
      "phase": "done",
      "step": 1000,
      "total": 1000,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": null,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": null,
      "last_sample_at": null,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": 6275.0,
      "finished_at": 9995.0,
      "updated_at": 9998.0,
      "cost": 4.5
    },
    "line": "✔ train-run  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  100%  done in 1h02m · $4.50",
    "rows": [
      "✔ train-run  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  100%  done in 1h02m · $4.50"
    ]
  },
  "stale": {
    "state": {
      "run": "train-run",
      "phase": "running",
      "step": 5,
      "total": 50,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": 60,
      "stage": null,
      "stage_since": null,
      "stage_index": null,
      "stage_count": null,
      "stages": [],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": null,
      "last_sample_at": null,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": null,
      "finished_at": null,
      "updated_at": 9880.0,
      "cost": null
    },
    "line": "▶ train-run  ▓▓░░░░░░░░░░░░░░░░░░   10%  ETA 1m00s · stale (poller not running?)",
    "rows": [
      "▶ train-run  ▓▓░░░░░░░░░░░░░░░░░░   10%  ETA 1m00s · stale (poller not running?)"
    ]
  },
  "stages": {
    "state": {
      "run": "train-run",
      "phase": "running",
      "step": 40,
      "total": 200,
      "epoch": null,
      "peak": null,
      "peak_epoch": null,
      "eta_s": 300,
      "stage": "train",
      "stage_since": 9900.0,
      "stage_index": 2,
      "stage_count": 3,
      "stages": [
        {
          "name": "setup",
          "start": 9500.0,
          "end": 9600.0,
          "attempt": 1
        },
        {
          "name": "train",
          "start": 9900.0,
          "end": null,
          "attempt": 1
        }
      ],
      "attempt_no": 1,
      "setbacks": [],
      "lost_s": 0.0,
      "last_error": null,
      "last_sample_at": null,
      "issue_since": null,
      "pending_since": null,
      "pending_note": null,
      "started_at": 9500.0,
      "finished_at": null,
      "updated_at": 9998.0,
      "cost": null
    },
    "line": "▶ train-run [train]  ▓▓▓▓░░░░░░░░░░░░░░░░   20%  ETA 5m00s",
    "rows": [
      "▶ train-run  ▓▓▓▓▓▓▓▓░░░░░░░░░░░░   40%  stage 2/3",
      "   ✔ setup  1m40s",
      "   ▸ train  ▓▓▓▓░░░░░░░░░░░░░░░░   20%  ETA 5m00s",
      "   · 1 more stage"
    ]
  }
}

describe('render', () => {
  for (const [name, c] of Object.entries(CASES)) {
    test(`${name} matches the status line`, () => {
      expect(renderLine(c.state, NOW).text).toBe(c.line)
      expect(renderRows(c.state, NOW).map(r => r.text)).toEqual(c.rows)
    })
  }

  test('a stale run is dimmed', () => {
    expect(renderLine(CASES.stale!.state, NOW).isStale).toBe(true)
    expect(renderLine(CASES.running!.state, NOW).isStale).toBe(false)
  })

  test('durations and bars', () => {
    expect(fmtDuration(59)).toBe('59s')
    expect(fmtDuration(3725)).toBe('1h02m')
    expect(bar(2, 2, 8, 4)).toBe('▓░░░')
    expect(bar(1, 1, 8, 4)).toBe('░░░░') // half a cell rounds to even, as Python's round()
    expect(bar(2, 6, 8, 4)).toBe('▓▒▒░')
  })

  test('long-finished runs drop out; live ones come first', () => {
    const old = { ...CASES.done!.state, run: 'old', finished_at: NOW - 3600 }
    const rows = renderAll([CASES.done!.state, old, CASES.running!.state], NOW)
    expect(rows.map(r => r.text.split('  ')[0])).toEqual(['▶ train-run', '✔ train-run'])
  })
})
