// "Rewrite with Claude" for the selected bullet, summary, title, skill list or text box.
import { useState } from 'react';
import type { CvData, Variant } from './types';
import type { Sel } from './Toolbar';
import { API, docQuery } from './model';
import { textField } from './fields';
import { richToHtml } from './rich';

type Update = (fn: (d: CvData) => void) => void;

const MODES: [string, string, string][] = [
  ['shorter', 'Shorter', 'About a third fewer words, same facts'],
  ['numbers', 'Add numbers', 'Concrete impact, only numbers from your profile'],
  ['verbs', 'Stronger verbs', 'Start with a specific action, drop filler'],
  ['job', 'Fit this job', 'Speak to the posting, honestly'],
];

export function RewritePanel({ data, variant, sel, update, close }: { data: CvData; variant: Variant; sel: Sel; update: Update; close: () => void }) {
  const field = textField(data, variant, sel);
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState<{ options: string[]; note: string } | null>(null);
  const [error, setError] = useState('');
  const [custom, setCustom] = useState('');
  if (!field) return null;
  const original = field.get();

  const run = async (mode: string, label: string) => {
    setBusy(label);
    setError('');
    setResult(null);
    try {
      const res = await fetch(`${API}rewrite${docQuery}`, { method: 'POST', body: JSON.stringify({ text: original, mode, custom: mode ? '' : custom }) });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="rewrite">
      <div className="rw-head">
        <strong>Rewrite this {field.label} with Claude</strong>
        <span className="hint">Uses only facts from your profile. Nothing changes until you pick a version.</span>
      </div>
      <div className="rw-modes">
        {MODES.filter(([m]) => m !== 'job' || data.mode === 'cv').map(([mode, label, hint]) => (
          <button key={mode} disabled={!!busy} title={hint} onClick={() => run(mode, label)}>
            {label}
          </button>
        ))}
      </div>
      <form
        className="rw-custom"
        onSubmit={(e) => {
          e.preventDefault();
          if (custom.trim()) run('', 'Your instruction');
        }}
      >
        <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Or say how, e.g. “mention the team size”" />
        <button className="primary" disabled={!!busy || !custom.trim()}>Go</button>
      </form>
      {busy && (
        <div className="rw-busy">
          <span className="spin" /> {busy}… Claude usually takes 10–30 seconds.
        </div>
      )}
      {error && <div className="rw-error">{error}</div>}
      {result && (
        <div className="rw-results">
          {result.options.map((o, i) => (
            <div key={i} className="rw-option">
              <div className="rw-text" dangerouslySetInnerHTML={{ __html: richToHtml(o) }} />
              <button
                className="primary"
                onClick={() => {
                  update((d) => field.set(d, o));
                  close();
                }}
              >
                Use this
              </button>
            </div>
          ))}
          {result.note && <p className="hint rw-note">{result.note}</p>}
          <p className="hint">Not right? Pick another option above, or Ctrl+Z after using one.</p>
        </div>
      )}
    </div>
  );
}
