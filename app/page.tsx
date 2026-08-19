'use client'

import { useState } from 'react'
import { Phone } from 'lucide-react'
import VoiceAgentModal from '../components/VoiceAgentModal'

export default function Home() {
  const [showAgent, setShowAgent] = useState(false)

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="text-center space-y-6 max-w-md">
        <h1 className="text-4xl font-bold text-text-primary">
          Corafone Recovery
        </h1>
        <p className="text-lg text-text-muted">
          Speak with our AI collections agent to resolve your account.
        </p>
        <button
          onClick={() => setShowAgent(true)}
          className="inline-flex items-center gap-2 px-8 py-4 bg-primary text-white font-semibold rounded-lg hover:bg-blue-600 transition-colors shadow-lg"
        >
          <Phone className="w-5 h-5" />
          Start Call
        </button>
        <p className="text-sm text-text-muted">
          This call may be recorded for compliance and quality assurance.
        </p>
      </div>

      <VoiceAgentModal
        isOpen={showAgent}
        onClose={() => setShowAgent(false)}
      />
    </main>
  )
}
