# Corafone Recovery — AI Collections Voice Agent

A self-contained Next.js 14 application that connects consumers to the `corafone-collector` LiveKit AI agent for debt collection calls.

This directory is **portable** — you can zip it, hand it to another team, and it will work with no references to the parent repository.

---

## What's in this directory

| File / Folder | Purpose |
|---|---|
| `agent.py` | Python LiveKit agent (debt collection logic, deployed separately via `lk agent deploy`) |
| `Dockerfile` | LiveKit Cloud agent container build |
| `livekit.toml` | LiveKit Cloud project config |
| `schema.sql` | Neon Postgres schema (agreements, compliance_breaches, consumers) |
| `requirements.txt` | Python dependencies for the agent |
| `test_policy.py` | Unit tests for the deterministic policy engine |
| **New Next.js app** | |
| `package.json` | Node.js dependencies (Next.js 14 + LiveKit SDKs) |
| `next.config.js` | Server mode config (**no** `output: 'export'`) |
| `vercel.json` | Vercel deployment config |
| `tsconfig.json`, `postcss.config.js`, `tailwind.config.ts` | Standard Next.js toolchain |
| `app/` | Next.js App Router pages + `/api/token` route |
| `components/` | `VoiceAgentModal.tsx`, `VoiceAgentContent.tsx` |
| `.env.local.template` | Environment variable template |

---

## Quick start (local)

```bash
# 1. Copy the env template and fill in your LiveKit Cloud credentials
cp .env.local.template .env.local

# 2. Install dependencies
npm install

# 3. Start dev server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and click **"Start Call"**.

---

## Environment variables

Create `.env.local` from the template and fill in **all** of these:

| Variable | Where to find it |
|---|---|
| `LIVEKIT_URL` | LiveKit Cloud Dashboard → Your Project → Settings → `wss://...` |
| `LIVEKIT_API_KEY` | LiveKit Cloud Dashboard → Settings → API Keys |
| `LIVEKIT_API_SECRET` | LiveKit Cloud Dashboard → Settings → API Keys |
| `NEXT_PUBLIC_LIVEKIT_AGENT_NAME` | Must match `agent_name` in `agent.py` line 785 — default is `corafone-collector` |
| `DATABASE_URL` | Neon Postgres connection string (used by `agent.py` only) |

**Never commit `.env.local` to git.** It is already in `.gitignore`.

---

## Deploy to Vercel

1. Create a **new** Vercel project and point it at this `corafone-agent/` directory (or the root if this is the whole repo).
2. Vercel will auto-detect Next.js.
3. Go to **Settings → Environment Variables** and add the 4 LiveKit variables above for **Production, Preview, and Development**.
4. Deploy.

**Critical rules — getting these wrong causes 404s:**
- Do **NOT** add `"framework": null` to `vercel.json`
- Do **NOT** add `"outputDirectory": "..."` to `vercel.json`
- Do **NOT** add `output: 'export'` to `next.config.js`
- The `/api/token` route **requires** server mode

---

## Architecture

```
Browser → VoiceAgentModal → POST /api/token → LiveKit Cloud → corafone-collector agent
                                        ↑
                                   AccessToken (JWT)
                                   signed with LIVEKIT_API_KEY
```

The web app sends an empty `participantAttributes` object. The Python agent reads defaults (`principal=1000`, `days_delinquent=180`) and then queries the `consumers` table via the `verify_identity` tool once the consumer speaks their name + last-4 SSN.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 404 on all pages | `"framework": null` in `vercel.json` | Remove it |
| "Routes Manifest Could Not Be Found" | `"outputDirectory"` in `vercel.json` | Remove it |
| 404 on `/api/token` | `output: 'export'` in `next.config.js` | Remove it |
| 500 "Server configuration error" | Missing `LIVEKIT_*` env vars on Vercel | Add all 4 in Dashboard |
| Agent not connecting | `NEXT_PUBLIC_LIVEKIT_AGENT_NAME` mismatch | Must match `agent.py` line 785 |
| "Click to enable audio" button | Browser autoplay policy | Expected — click the button |

---

## Agent deployment (separate from Vercel)

The `corafone-collector` agent (`agent.py`) is **not** deployed through Vercel. It runs on LiveKit Cloud:

```bash
# From this directory
lk agent deploy
```

See the [LiveKit Agents docs](https://docs.livekit.io/agents/) for details.

---

## Support

This app was generated from the [livekit-voice-agent skill](.opencode/skills/livekit-voice-agent/SKILL.md) (if present in the parent repo).
# corafone-test
