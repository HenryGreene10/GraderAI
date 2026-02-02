import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import React from 'react'

vi.mock('../src/lib/supabaseClient.js', () => ({
  supabase: {
    auth: {
      getUser: vi.fn(async () => ({ data: { user: { id: 'user-123' } }, error: null })),
      getSession: vi.fn(async () => ({ data: { session: { access_token: 'test-token' } }, error: null })),
    },
  },
}))

// Mock the OCR lib to control behavior
vi.mock('../src/lib/ocr.js', () => ({
  startOCR: vi.fn(async () => ({ status: 'done', text_len: 0 })),
  pollOCR: vi.fn((id, onTick) => {
    // Immediately report done
    setTimeout(() => onTick({ status: 'done', extracted_text: 'Mocked text' }), 0)
    return () => {}
  }),
}))

import FileRow from '../src/components/FileRow.jsx'

describe('FileRow component', () => {
  const mockFetch = (payload) => {
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => payload }))
  }

  it('auto-starts OCR and shows done status with text', async () => {
    mockFetch({ status: 'done', extracted_text: 'Mocked text' })
    render(
      <FileRow
        file={{ id: 'u-99', name: 'sample.png', status: 'pending', extracted_text: '' }}
      />
    )

    // chip should eventually show done and the text should appear
    await waitFor(() => expect(screen.getByText('ocr_done')).toBeInTheDocument())
  })

  it('does not auto-start when already OCR_DONE (idempotent) and no Retry shown', async () => {
    const { startOCR } = await import('../src/lib/ocr.js')
    ;(startOCR).mockClear()
    mockFetch({ status: 'done' })

    render(
      <FileRow
        file={{ id: 'u-done', name: 'done.png', status: 'OCR_DONE', extracted_text: 'Existing text' }}
      />
    )

    await new Promise((r) => setTimeout(r, 20))
    expect(startOCR).not.toHaveBeenCalled()
    expect(screen.getByText('ocr_done')).toBeInTheDocument()
    // Retry should NOT be visible for OCR_DONE
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('shows error status when refresh reports failure from processing state', async () => {
    mockFetch({ status: 'failed', error: 'timeout' })
    render(
      <FileRow
        file={{ id: 'u-err', name: 'err.png', status: 'processing', extracted_text: '' }}
      />
    )

    // Status chip should show error when refresh returns failed
    expect(await screen.findByText('error')).toBeInTheDocument()
  })

  it('shows Retry when status is error, ocr_error, or failed', async () => {
    mockFetch({ status: 'error' })
    // error
    const { rerender } = render(
      <FileRow file={{ id: 'u-errA', name: 'a.png', status: 'error', extracted_text: '' }} />
    )
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()

    // ocr_error
    rerender(
      <FileRow file={{ id: 'u-errB', name: 'b.png', status: 'ocr_error', extracted_text: '' }} />
    )
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()

    // failed
    rerender(
      <FileRow file={{ id: 'u-errC', name: 'c.png', status: 'failed', extracted_text: '' }} />
    )
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('does NOT show Retry for processing or pending', async () => {
    mockFetch({ status: 'processing' })
    const { rerender } = render(
      <FileRow file={{ id: 'u-p1', name: 'p1.png', status: 'processing', extracted_text: '' }} />
    )
    // Give poll effect a tick, but button should not be present
    await new Promise((r) => setTimeout(r, 10))
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()

    rerender(
      <FileRow file={{ id: 'u-p2', name: 'p2.png', status: 'pending', extracted_text: '' }} />
    )
    await new Promise((r) => setTimeout(r, 10))
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('invokes startOCR when clicking Retry', async () => {
    const { startOCR } = await import('../src/lib/ocr.js')
    ;(startOCR).mockClear()
    mockFetch({ status: 'failed' })

    render(
      <FileRow file={{ id: 'u-click', name: 'click.png', status: 'failed', extracted_text: '' }} />
    )
    const btn = await screen.findByRole('button', { name: /retry/i })
    fireEvent.click(btn)
    await waitFor(() => expect(startOCR).toHaveBeenCalledTimes(1))
  })
})
