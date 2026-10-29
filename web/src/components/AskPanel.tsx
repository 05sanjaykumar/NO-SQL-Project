// Ask the running system a question in plain English.
//
// The rest of the dashboard answers the questions it was designed for. This one
// answers the others: the question goes to the Claude Code CLI on the host, which
// queries the live detector through the fraudsim MCP tools and reports what it
// found. The tools it called are shown with the answer, so you can see it really
// queried the system rather than inventing a plausible number.

import { useEffect, useRef, useState } from 'react';
import { getJSON, postJSON } from '../api';

type Status = { available: boolean; reason: string | null; busy?: boolean };
type Answer = { answer: string; tools_used: string[]; cost_usd: number | null; duration_s: number | null };

const SUGGESTIONS = [
  'How many rings are flagged, and what is the current recall?',
  'Inject a shared_device ring with no evasion, wait, then tell me if it was caught.',
  'Now inject the same ring with low_and_slow evasion and compare.',
  'Which current alerts are false alarms? Check against the oracle.',
];

export default function AskPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getJSON<Status>('/assistant/status').then(setStatus).catch(() => setStatus({ available: false, reason: 'The assistant endpoint is not reachable.' }));
  }, []);

  const ask = async (q: string) => {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true); setError(null); setAnswer(null); setQuestion(text);
    try {
      setAnswer(await postJSON<Answer>('/assistant/ask', { question: text }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  };

  return (
    <section className="panel ask-panel">
      <div className="panel-h">
        <h3>Ask the system</h3>
        <span className="hint">plain English · answered from the live run</span>
      </div>

      {status && !status.available ? (
        <p className="ask-unavailable">{status.reason}</p>
      ) : (
        <>
          <div className="ask-suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="btn small" disabled={busy} onClick={() => ask(s)} title={s}>
                {s.length > 44 ? `${s.slice(0, 44)}…` : s}
              </button>
            ))}
          </div>

          <textarea
            ref={input}
            className="ask-input"
            rows={2}
            placeholder="Ask anything about the run — press Enter to send"
            value={question}
            disabled={busy}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(question); }
            }}
          />
          <div className="ask-actions">
            <button className="btn" disabled={busy || !question.trim()} onClick={() => ask(question)}>
              {busy ? 'Thinking…' : 'Ask'}
            </button>
            <span className="hint">each question costs a little against your Claude account</span>
          </div>
        </>
      )}

      {busy && <p className="ask-thinking">Querying the live detector…</p>}
      {error && <p className="ask-error">{error}</p>}

      {answer && (
        <div className="ask-answer">
          <p>{answer.answer}</p>
          {answer.tools_used.length > 0 && (
            <div className="ask-tools">
              {answer.tools_used.map((t) => <span key={t} className="ask-tool">{t}</span>)}
            </div>
          )}
          <span className="hint">
            {answer.duration_s != null && `${answer.duration_s}s`}
            {answer.cost_usd != null && ` · $${answer.cost_usd.toFixed(3)}`}
          </span>
        </div>
      )}
    </section>
  );
}
