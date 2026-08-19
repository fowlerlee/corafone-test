'use client'

import { useEffect, useCallback, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { TokenSource } from 'livekit-client'
import { SessionProvider, useSession, useStartAudio, RoomAudioRenderer } from '@livekit/components-react'
import { X, Mic, MicOff, PhoneOff, Volume2 } from 'lucide-react'
import VoiceAgentContent from './VoiceAgentContent'
import type { UseSessionReturn } from '@livekit/components-react'

interface VoiceAgentModalProps {
  isOpen: boolean
  onClose: () => void
}

export default function VoiceAgentModal({ isOpen, onClose }: VoiceAgentModalProps) {
  if (!isOpen) return null

  return (
    <AnimatePresence>
      <ModalOverlay onClose={onClose} />
    </AnimatePresence>
  )
}

function ModalOverlay({ onClose }: { onClose: () => void }) {
  const tokenUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/api/token`
    : '/api/token'

  const tokenSource = useMemo(() => TokenSource.endpoint(tokenUrl), [tokenUrl])

  const agentName = process.env.NEXT_PUBLIC_LIVEKIT_AGENT_NAME || 'corafone-collector'

  const sessionOptions = useMemo(() => ({
    agentName,
    roomName: `corafone-${Date.now()}`,
    participantName: 'Consumer',
    participantIdentity: `consumer-${Date.now()}`,
    participantAttributes: {},
  }), [agentName])

  const session: UseSessionReturn = useSession(tokenSource, sessionOptions)

  useEffect(() => {
    if (session.isConnected) return

    session.start({
      tracks: {
        microphone: { enabled: true },
      },
    })
  }, [session])

  useEffect(() => {
    return () => {
      if (session.isConnected) {
        session.end().catch(console.error)
      }
    }
  }, [])

  const handleDisconnect = useCallback(() => {
    if (session.isConnected) {
      session.end().catch(console.error)
    }
    onClose()
  }, [session, onClose])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm"
      onClick={handleDisconnect}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.9, opacity: 0, y: 20 }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="relative w-full max-w-lg bg-surface rounded-2xl border border-border shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-6 border-b border-border bg-surface/50">
          <div>
            <h3 className="text-xl font-semibold text-text-primary">Corafone Recovery</h3>
            <p className="text-sm text-text-muted">
              Speak with our AI collections agent
            </p>
          </div>
          <button
            onClick={handleDisconnect}
            className="p-2 text-text-muted hover:text-text-primary hover:bg-surface-2 rounded-lg transition-colors"
            aria-label="Close modal"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <SessionProvider session={session}>
          <RoomAudioRenderer />
          <div className="p-6">
            <StartAudioButton session={session} />
            <VoiceAgentContent />
          </div>
        </SessionProvider>

        <div className="flex items-center justify-between p-6 border-t border-border bg-surface/50">
          <MicControl session={session} />

          <button
            onClick={handleDisconnect}
            className="flex items-center gap-2 px-4 py-2 text-error hover:bg-error/10 rounded-lg transition-colors"
          >
            <PhoneOff className="w-4 h-4" />
            End Call
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

function StartAudioButton({ session }: { session: UseSessionReturn }) {
  const { mergedProps, canPlayAudio } = useStartAudio({
    room: session.room,
    props: {
      className:
        'w-full py-3 px-6 mb-4 bg-primary text-white font-semibold rounded-lg hover:bg-blue-600 transition-colors flex items-center justify-center gap-2',
    },
  })

  if (canPlayAudio) return null

  return (
    <button {...mergedProps}>
      <Volume2 className="w-5 h-5" />
      Click to enable audio
    </button>
  )
}

function MicControl({ session }: { session: UseSessionReturn }) {
  const [isMuted, setIsMuted] = useState(false)

  const toggleMic = useCallback(async () => {
    if (session.room?.localParticipant) {
      try {
        await session.room.localParticipant.setMicrophoneEnabled(!isMuted)
        setIsMuted(!isMuted)
      } catch (err) {
        console.error('Failed to toggle microphone:', err)
      }
    }
  }, [session, isMuted])

  return (
    <button
      onClick={toggleMic}
      disabled={!session.isConnected}
      className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
        isMuted
          ? 'bg-error/10 text-error'
          : 'bg-primary/10 text-primary'
      } ${!session.isConnected ? 'opacity-50 cursor-not-allowed' : 'hover:bg-opacity-20'}`}
    >
      {isMuted ? (
        <>
          <MicOff className="w-5 h-5" />
          <span className="text-sm font-medium">Unmute</span>
        </>
      ) : (
        <>
          <Mic className="w-5 h-5" />
          <span className="text-sm font-medium">Mute</span>
        </>
      )}
    </button>
  )
}
