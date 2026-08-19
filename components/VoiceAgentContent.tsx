'use client'

import { useAgent } from '@livekit/components-react'
import { Loader2, Mic, Brain, CheckCircle, AlertCircle } from 'lucide-react'

export default function VoiceAgentContent() {
  const agent = useAgent()

  if (agent.isFinished) {
    if (agent.failureReasons && agent.failureReasons.length > 0) {
      return <ErrorState reasons={agent.failureReasons} />
    }
    return <DisconnectedState />
  }

  if (agent.state === 'connecting' || agent.state === 'initializing') {
    return <ConnectingState />
  }

  if (agent.state === 'listening' || agent.state === 'idle') {
    return <ListeningState />
  }

  if (agent.state === 'thinking') {
    return <ThinkingState />
  }

  if (agent.state === 'speaking') {
    return <SpeakingState />
  }

  return <ConnectingState />
}

function ConnectingState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-4">
      <Loader2 className="w-12 h-12 text-primary animate-spin" />
      <p className="text-lg text-text-primary font-medium">Connecting to AI agent...</p>
      <p className="text-sm text-text-muted">This will take just a moment</p>
    </div>
  )
}

function ListeningState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-6">
      <div className="relative">
        <div className="absolute inset-0 bg-primary/20 rounded-full animate-ping" />
        <div className="w-20 h-20 rounded-full bg-primary/10 flex items-center justify-center relative">
          <Mic className="w-10 h-10 text-primary" />
        </div>
      </div>
      <div className="text-center space-y-2">
        <p className="text-lg text-text-primary font-medium">Listening...</p>
        <p className="text-sm text-text-muted">Speak now. The AI is ready to hear you.</p>
      </div>
    </div>
  )
}

function ThinkingState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-6">
      <div className="relative">
        <Brain className="w-16 h-16 text-accent animate-pulse" />
      </div>
      <div className="text-center space-y-2">
        <p className="text-lg text-text-primary font-medium">Processing...</p>
        <p className="text-sm text-text-muted">The AI is thinking about your answer</p>
      </div>
    </div>
  )
}

function SpeakingState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-6">
      <div className="relative">
        <div className="flex items-center justify-center space-x-1">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-3 bg-accent rounded-full animate-pulse"
              style={{
                height: `${20 + Math.random() * 40}px`,
                animationDelay: `${i * 0.1}s`,
                animationDuration: '0.6s',
              }}
            />
          ))}
        </div>
      </div>
      <div className="text-center space-y-2">
        <p className="text-lg text-text-primary font-medium">Speaking...</p>
        <p className="text-sm text-text-muted">The AI is responding to you</p>
      </div>
    </div>
  )
}

function DisconnectedState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-6">
      <div className="w-16 h-16 rounded-full bg-success/10 flex items-center justify-center">
        <CheckCircle className="w-10 h-10 text-success" />
      </div>
      <div className="text-center space-y-2">
        <p className="text-lg text-text-primary font-medium">Call completed!</p>
        <p className="text-sm text-text-muted max-w-sm">
          Thank you for speaking with us. Your case has been noted and will be reviewed.
        </p>
      </div>
    </div>
  )
}

function ErrorState({ reasons }: { reasons: string[] }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-4">
      <div className="w-16 h-16 rounded-full bg-error/10 flex items-center justify-center">
        <AlertCircle className="w-10 h-10 text-error" />
      </div>
      <div className="text-center space-y-2">
        <p className="text-lg text-text-primary font-medium">Connection failed</p>
        <p className="text-sm text-text-muted max-w-sm">
          {reasons.join(', ') || 'Unable to connect to the AI agent. Please try again later.'}
        </p>
      </div>
    </div>
  )
}
