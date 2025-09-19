import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../src/lib/supabaseClient.js', () => {
  return {
    supabase: {
      auth: {
        getUser: vi.fn(async () => ({ data: { user: { id: 'user-123' } }, error: null })),
      },
    },
  }
})

describe('lib/ocr.js', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('startOCR sends expected request', async () => {
    const calls = []
    global.fetch = vi.fn(async (url, init) => {
      calls.push({ url, init })
      return { ok: true, json: async () => ({ ok: true }) }
    })

    const { startOCR } = await import('../src/lib/ocr.js')
    const res = await startOCR('u-1')
    expect(res.ok).toBe(true)

    const req = calls.at(-1)
    expect(req.url).toMatch(/\/api\/ocr\/start$/)
    const body = JSON.parse(req.init.body)
    expect(body).toEqual({ upload_id: 'u-1' })
    expect(req.init.headers['X-Owner-Id']).toBe('user-123')
    expect(req.init.headers['X-User-Id']).toBe('user-123')
  })

  it('pollOCR calls onTick until done', async () => {
    const responses = [
      { status: 'processing' },
      { status: 'done', extracted_text: 'Hello OCR' },
    ]
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => responses.shift() }))

    const { pollOCR } = await import('../src/lib/ocr.js')
    const onTick = vi.fn()
    const stop = pollOCR('u-1', onTick, 5)

    // Wait a little for the IIFE to run through two loops
    await new Promise((r) => setTimeout(r, 40))
    stop()

    expect(onTick).toHaveBeenCalled()
    const last = onTick.mock.calls.at(-1)[0]
    expect(last.status).toBe('done')
    expect(last.extracted_text).toBe('Hello OCR')
  })

  it('pollOCR normalizes {state:"ERROR"} and stops', async () => {
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ state: 'ERROR', message: 'timeout' }) }))

    const { pollOCR } = await import('../src/lib/ocr.js')
    const onTick = vi.fn()
    const stop = pollOCR('u-err', onTick, 5)

    await new Promise((r) => setTimeout(r, 40))
    stop()

    // Should have emitted a failed status with error message
    const last = onTick.mock.calls.at(-1)[0]
    expect(last.status).toBe('failed')
    expect(String(last.error)).toContain('timeout')
  })
})
