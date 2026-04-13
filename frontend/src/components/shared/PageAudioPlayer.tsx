import { useEffect, useRef, useState, useCallback, type RefObject } from 'react'

const CHUNK_WORD_LIMIT = 180
const LS_VOICE = 'audio_player_voice'
const LS_RATE = 'audio_player_rate'

// Chrome: delay between cancel() and speak() to avoid the synth getting stuck
const SPEAK_DELAY_MS = 50
// Chrome: if onstart hasn't fired within this time, assume synth is stuck and retry
const WATCHDOG_MS = 500
// Chrome keepalive: pause/resume every N ms to prevent the engine going silent
const KEEPALIVE_MS = 10_000

function extractSections(container: HTMLElement): { text: string; el: Element | null }[] {
  const sections: { text: string; el: Element | null }[] = []
  const readableSelectors = ['p', 'li', 'h2', 'h3', 'h4', 'dt', 'dd']
  const elements = Array.from(container.querySelectorAll(readableSelectors.join(',')))
  const header = container.querySelector('.mb-6')

  for (const el of elements) {
    if (header && header.contains(el)) continue
    if (el.closest('table')) continue
    if (el.closest('pre') || el.closest('code')) continue
    const raw = el.textContent?.trim() ?? ''
    if (!raw) continue
    sections.push({ text: raw, el })
  }

  // Replace tables with a spoken placeholder, in DOM order
  for (const table of Array.from(container.querySelectorAll('table'))) {
    if (header && header.contains(table)) continue
    sections.push({ text: 'See the table on screen for details.', el: table })
  }

  sections.sort((a, b) => {
    if (!a.el || !b.el) return 0
    const pos = a.el.compareDocumentPosition(b.el)
    return pos & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
  })

  return sections
}

interface Props {
  contentRef: RefObject<HTMLElement | null>
}

export function PageAudioPlayer({ contentRef }: Props) {
  const [supported] = useState(() => 'speechSynthesis' in window)
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([])
  const [selectedVoiceURI, setSelectedVoiceURI] = useState<string>(
    () => localStorage.getItem(LS_VOICE) ?? ''
  )
  const [rate, setRate] = useState<number>(() => {
    const saved = localStorage.getItem(LS_RATE)
    return saved ? parseFloat(saved) : 1.0
  })
  const [playing, setPlaying] = useState(false)
  const [chunkIndex, setChunkIndex] = useState(0)
  const [totalChunks, setTotalChunks] = useState(0)

  const chunksRef = useRef<string[]>([])
  const chunkElsRef = useRef<(Element | null)[]>([])
  const currentChunkRef = useRef(0)
  const isPlayingRef = useRef(false)
  const rateRef = useRef(rate)
  const voiceURIRef = useRef(selectedVoiceURI)
  const prevHighlightRef = useRef<Element | null>(null)
  // Tracks the active watchdog so we can cancel it if onstart fires in time
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { rateRef.current = rate }, [rate])
  useEffect(() => { voiceURIRef.current = selectedVoiceURI }, [selectedVoiceURI])

  // Load voices; auto-select Google US English if no saved preference
  useEffect(() => {
    if (!supported) return
    const load = () => {
      const v = window.speechSynthesis.getVoices()
      if (!v.length) return
      setVoices(v)
      if (!localStorage.getItem(LS_VOICE)) {
        const google = v.find(voice => voice.name === 'Google US English')
        if (google) {
          setSelectedVoiceURI(google.voiceURI)
          voiceURIRef.current = google.voiceURI
        }
      }
    }
    load()
    window.speechSynthesis.onvoiceschanged = load
    return () => { window.speechSynthesis.onvoiceschanged = null }
  }, [supported])

  // Chrome keepalive: calling pause/resume prevents the engine going silent during long sessions
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      if (window.speechSynthesis.speaking) {
        window.speechSynthesis.pause()
        window.speechSynthesis.resume()
      }
    }, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [playing])

  const clearHighlight = useCallback(() => {
    if (prevHighlightRef.current) {
      const el = prevHighlightRef.current as HTMLElement
      el.style.backgroundColor = ''
      el.style.transition = ''
      el.style.borderRadius = ''
      prevHighlightRef.current = null
    }
  }, [])

  const applyHighlight = useCallback((el: Element | null) => {
    clearHighlight()
    if (!el) return
    const htmlEl = el as HTMLElement
    htmlEl.style.transition = 'background-color 0.3s ease'
    htmlEl.style.backgroundColor = 'color-mix(in srgb, var(--accent) 12%, transparent)'
    htmlEl.style.borderRadius = '4px'
    prevHighlightRef.current = el
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [clearHighlight])

  // Speak a single chunk, with Chrome-safe cancel-delay-speak and watchdog retry
  const speakChunk = useCallback((index: number, isRetry = false) => {
    if (!isPlayingRef.current) return
    if (index >= chunksRef.current.length) {
      isPlayingRef.current = false
      setPlaying(false)
      clearHighlight()
      return
    }

    const text = chunksRef.current[index]
    const el = chunkElsRef.current[index] ?? null

    applyHighlight(el)
    currentChunkRef.current = index
    setChunkIndex(index + 1)

    // Cancel any prior speech, then wait briefly before speaking — Chrome requires this gap
    window.speechSynthesis.cancel()

    const doSpeak = () => {
      if (!isPlayingRef.current) return

      const utterance = new SpeechSynthesisUtterance(text)
      utterance.rate = rateRef.current

      const voice = window.speechSynthesis.getVoices().find(
        v => v.voiceURI === voiceURIRef.current
      )
      if (voice) utterance.voice = voice

      // Watchdog: if onstart doesn't fire, Chrome's synth is stuck — retry once
      if (watchdogRef.current) clearTimeout(watchdogRef.current)
      if (!isRetry) {
        watchdogRef.current = setTimeout(() => {
          watchdogRef.current = null
          if (isPlayingRef.current && !window.speechSynthesis.speaking) {
            // Synth is stuck: force a hard cancel then retry this chunk
            window.speechSynthesis.cancel()
            setTimeout(() => speakChunk(index, true), 100)
          }
        }, WATCHDOG_MS)
      }

      utterance.onstart = () => {
        if (watchdogRef.current) {
          clearTimeout(watchdogRef.current)
          watchdogRef.current = null
        }
      }

      utterance.onend = () => {
        if (isPlayingRef.current) {
          speakChunk(index + 1)
        }
      }

      utterance.onerror = (e) => {
        if (watchdogRef.current) {
          clearTimeout(watchdogRef.current)
          watchdogRef.current = null
        }
        // 'interrupted' fires on cancel() — that's normal, not an error
        if (e.error !== 'interrupted' && isPlayingRef.current) {
          isPlayingRef.current = false
          setPlaying(false)
          clearHighlight()
        }
      }

      window.speechSynthesis.speak(utterance)
    }

    setTimeout(doSpeak, SPEAK_DELAY_MS)
  }, [applyHighlight, clearHighlight])

  const buildChunks = useCallback(() => {
    if (!contentRef.current) return false
    const sections = extractSections(contentRef.current)
    const allChunks: string[] = []
    const allEls: (Element | null)[] = []

    for (const { text, el } of sections) {
      const words = text.split(/\s+/).filter(Boolean)
      if (words.length === 0) continue
      if (words.length <= CHUNK_WORD_LIMIT) {
        allChunks.push(text)
        allEls.push(el)
      } else {
        for (let i = 0; i < words.length; i += CHUNK_WORD_LIMIT) {
          allChunks.push(words.slice(i, i + CHUNK_WORD_LIMIT).join(' '))
          allEls.push(el)
        }
      }
    }

    chunksRef.current = allChunks
    chunkElsRef.current = allEls
    setTotalChunks(allChunks.length)
    return allChunks.length > 0
  }, [contentRef])

  const handlePlay = useCallback(() => {
    if (playing) {
      // "Pause" = cancel + remember position. We avoid synth.pause()/resume()
      // because Chrome leaves the engine in a broken state after using them.
      window.speechSynthesis.cancel()
      isPlayingRef.current = false
      setPlaying(false)
      return
    }

    // Resume from remembered position, or fresh start
    const resumeIndex = chunksRef.current.length > 0 ? currentChunkRef.current : -1

    if (resumeIndex < 0) {
      // Fresh start
      const ok = buildChunks()
      if (!ok) return
      currentChunkRef.current = 0
    }

    isPlayingRef.current = true
    setPlaying(true)
    speakChunk(resumeIndex < 0 ? 0 : resumeIndex)
  }, [playing, buildChunks, speakChunk])

  const handleStop = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current)
      watchdogRef.current = null
    }
    isPlayingRef.current = false
    window.speechSynthesis.cancel()
    setPlaying(false)
    setChunkIndex(0)
    // Reset so next Play is always a fresh start
    chunksRef.current = []
    chunkElsRef.current = []
    currentChunkRef.current = 0
    clearHighlight()
  }, [clearHighlight])

  const handleVoiceChange = (uri: string) => {
    setSelectedVoiceURI(uri)
    localStorage.setItem(LS_VOICE, uri)
  }

  const handleRateChange = (r: number) => {
    setRate(r)
    rateRef.current = r
    localStorage.setItem(LS_RATE, String(r))
  }

  // Clean up on page navigation (component unmount)
  useEffect(() => {
    return () => {
      if (watchdogRef.current) clearTimeout(watchdogRef.current)
      isPlayingRef.current = false
      window.speechSynthesis.cancel()
      clearHighlight()
    }
  }, [clearHighlight])

  if (!supported) return null

  const progress = totalChunks > 0 ? `${chunkIndex} / ${totalChunks}` : '—'

  return (
    <div
      className="mt-10 rounded-lg border px-4 py-3 flex flex-wrap items-center gap-3 text-sm"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent) 18%, #0a0b10)',
        borderColor: 'color-mix(in srgb, var(--accent) 45%, transparent)',
        color: 'var(--text-secondary)',
        position: 'sticky',
        bottom: '1rem',
      }}
    >
      {/* Play/Pause */}
      <button
        onClick={handlePlay}
        className="rounded px-3 py-1 font-medium text-xs"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 18%, transparent)',
          color: 'var(--accent)',
          border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)',
        }}
        title={playing ? 'Pause' : 'Play'}
      >
        {playing ? '⏸ Pause' : '▶ Play'}
      </button>

      {/* Stop */}
      <button
        onClick={handleStop}
        className="rounded px-3 py-1 font-medium text-xs"
        style={{
          backgroundColor: 'var(--bg-secondary)',
          color: 'var(--text-muted)',
          border: '1px solid var(--border)',
        }}
        title="Stop"
      >
        ■ Stop
      </button>

      {/* Progress */}
      <span
        className="text-xs tabular"
        style={{ color: 'var(--text-muted)', minWidth: '7rem' }}
      >
        {playing || chunkIndex > 0
          ? `Reading ${progress}`
          : '🔊 Listen to this page'}
      </span>

      {/* Voice picker */}
      {voices.length > 0 && (
        <select
          value={selectedVoiceURI}
          onChange={e => handleVoiceChange(e.target.value)}
          className="rounded text-xs px-2 py-1"
          style={{
            backgroundColor: 'var(--bg-secondary)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border)',
            maxWidth: '180px',
          }}
          title="Select voice"
        >
          <option value="">Default voice</option>
          {voices.map(v => (
            <option key={v.voiceURI} value={v.voiceURI}>
              {v.name}
            </option>
          ))}
        </select>
      )}

      {/* Rate slider */}
      <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        Speed
        <input
          type="range"
          min={0.8}
          max={1.5}
          step={0.1}
          value={rate}
          onChange={e => handleRateChange(parseFloat(e.target.value))}
          style={{ accentColor: 'var(--accent)', width: '80px' }}
          title={`${rate}x`}
        />
        <span className="tabular" style={{ minWidth: '2.5rem' }}>{rate.toFixed(1)}×</span>
      </label>
    </div>
  )
}
