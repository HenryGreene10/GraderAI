import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import React from 'react'

// Mock the OCR lib to control behavior
vi.mock('../src/lib/ocr.js', () => ({
  startOCR: vi.fn(async () => ({ ok: true })),
  pollOCR: vi.fn((id, onTick) => {
    // Immediately report done
    setTimeout(() => onTick({ status: 'done', extracted_text: 'Mocked text' }), 0)
    return () => {}
  }),
}))

import FileRow from '../src/components/FileRow.jsx'

describe('FileRow component', () => {
  it('auto-starts OCR and shows done status with text', async () => {
    render(
      <FileRow
        file={{ id: 'u-99', name: 'sample.png', status: 'pending', extracted_text: '' }}
      />
    )

    // chip should eventually show done and the text should appear
    await waitFor(() => expect(screen.getByText('done')).toBeInTheDocument())
    expect(screen.getByText('Mocked text')).toBeInTheDocument()
  })

  it('does not auto-start when already OCR_DONE (idempotent)', async () => {
    const { startOCR } = await import('../src/lib/ocr.js')
    ;(startOCR).mockClear()

    render(
      <FileRow
        file={{ id: 'u-done', name: 'done.png', status: 'OCR_DONE', extracted_text: 'Existing text' }}
      />
    )

    await new Promise((r) => setTimeout(r, 20))
    expect(startOCR).not.toHaveBeenCalled()
    // Existing text remains visible
    expect(screen.getByText('Existing text')).toBeInTheDocument()
  })

  it('displays error when polling reports failure from processing state', async () => {
    const { pollOCR } = await import('../src/lib/ocr.js')
    ;(pollOCR).mockImplementation((id, onTick) => {
      setTimeout(() => onTick({ status: 'failed', error: 'timeout' }), 0)
      return () => {}
    })

    render(
      <FileRow
        file={{ id: 'u-err', name: 'err.png', status: 'processing', extracted_text: '' }}
      />
    )

    // Error text should appear from polling failure
    expect(await screen.findByText(/timeout/i)).toBeInTheDocument()
  })

  it('shows retry action when OCR_ERROR', async () => {
    render(
      <FileRow
        file={{ id: 'u-err2', name: 'err2.png', status: 'OCR_ERROR', extracted_text: '' }}
      />
    )

    // Retry button should be visible for non-processing state
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
