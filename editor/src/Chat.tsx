// Chat with Claude about the CV on the page: ask for suggestions, or for changes ("replace the projects"). A
// change is a proposal: it shows what would change, and Apply makes it one undo step.
import { useEffect, useRef, useState } from 'react';
import type { Bullet, Content, CvData, Variant } from './types';
import { API, docQuery, uid } from './model';
import { richToHtml } from './rich';

type Update = (fn: (d: CvData) => void) => void;

interface Message {
  role: 'user' | 'assistant';
  text: string;
  changes?: string[];
  /** The proposed content and the content it was made from (as this version shows it). */
  content?: Content | null;
  base?: string;
  state?: '' | 'applied' | 'dismissed';
}

const STORE = `cvmaker-editor-chat${docQuery}`;
const SUGGESTIONS = [
  'What would you improve on this CV?',
  'Make the summary shorter and sharper',
  'Which bullets are weakest, and why?',
  'Suggest better projects for this CV',
];

function load(): Message[] {
  try {
    return JSON.parse(sessionStorage.getItem(STORE) || '[]');
  } catch {
    return [];
  }
}

function save(messages: Message[]) {
  try {
    sessionStorage.setItem(STORE, JSON.stringify(messages.slice(-40)));
  } catch {
    /* storage full or blocked: the chat still works for this visit */
  }
}

/** Fields a version can keep its own wording for, as [override key, read, write]. */
function ownedFields(c: Content): [string, () => string, (v: string) => void][] {
  const out: [string, () => string, (v: string) => void][] = [
    ['title', () => c.title, (v) => void (c.title = v)],
    ['summary', () => c.summary, (v) => void (c.summary = v)],
  ];
  const bullets = (b: Bullet) => out.push([`b:${b.id}`, () => b.text, (v) => void (b.text = v)]);
  for (const e of c.experience) e.bullets.forEach(bullets);
  for (const p of c.projects) {
    p.bullets.forEach(bullets);
    out.push([`tech:${p.id}`, () => p.tech, (v) => void (p.tech = v)]);
  }
  for (const g of c.skills) out.push([`skill:${g.id}`, () => g.value, (v) => void (g.value = v)]);
  for (const e of c.education) out.push([`degree:${e.id}`, () => e.degree, (v) => void (e.degree = v)]);
  return out;
}

/** The content as this version shows it: its own wording in place of the shared text. */
export function shownContent(data: CvData, variant: Variant): Content {
  const c = structuredClone(data.content);
  for (const [key, , set] of ownedFields(c)) if (key in variant.overrides) set(variant.overrides[key]);
  return c;
}

/** New and duplicate ids (Claude names new items "new-…") become real ones. */
function freshIds(c: Content, taken: Set<string>) {
  const seen = new Set<string>();
  const fix = (item: { id: string }, prefix: string) => {
    if (!item.id || seen.has(item.id) || (item.id.startsWith('new') && !taken.has(item.id))) item.id = uid(prefix);
    seen.add(item.id);
  };
  const bullets = (list: Bullet[]) => list.forEach((b) => fix(b, 'b'));
  for (const e of c.experience) (fix(e, 'job'), bullets(e.bullets ?? (e.bullets = [])));
  for (const p of c.projects) (fix(p, 'proj'), bullets(p.bullets ?? (p.bullets = [])));
  c.skills.forEach((g) => fix(g, 'skill'));
  c.education.forEach((e) => fix(e, 'edu'));
  for (const s of c.custom) {
    fix(s, 'sec');
    for (const e of s.entries ?? (s.entries = [])) (fix(e, 'ent'), bullets(e.bullets ?? (e.bullets = [])));
  }
}

function allIds(c: Content): Set<string> {
  const ids = new Set<string>();
  const add = (x: { id: string }) => ids.add(x.id);
  for (const e of c.experience) (add(e), e.bullets.forEach(add));
  for (const p of c.projects) (add(p), p.bullets.forEach(add));
  c.skills.forEach(add);
  c.education.forEach(add);
  for (const s of c.custom) (add(s), s.entries.forEach((e) => (add(e), e.bullets.forEach(add))));
  return ids;
}

/** Put proposed content (as this version shows it) into the document: text this version words its own way goes
 *  into its overrides, so the shared profile text only changes where the version uses it. */
export function applyProposal(d: CvData, variantId: string, proposed: Content) {
  const vi = d.variants.findIndex((v) => v.id === variantId);
  const next = structuredClone(proposed);
  freshIds(next, allIds(d.content));
  if (vi >= 0) {
    const overrides = d.variants[vi].overrides;
    const before = new Map(ownedFields(structuredClone(d.content)).map(([k, get]) => [k, get()]));
    for (const [key, get, set] of ownedFields(next)) {
      if (!(key in overrides)) continue;
      overrides[key] = get();
      if (before.has(key)) set(before.get(key)!);
    }
  }
  d.content = next;
}

/** The CV as lines of text, to show what a proposal adds and removes. */
function lines(c: Content): string[] {
  const out = [`Name: ${c.name}`, `Title: ${c.title}`, `Summary: ${c.summary}`, ...c.contact.map((l) => `Contact: ${l}`)];
  for (const e of c.experience) {
    out.push(`Job: ${e.role} · ${e.company} · ${e.dates}`);
    for (const b of e.bullets) out.push(`  ${e.company}: ${b.text}`);
  }
  for (const p of c.projects) {
    out.push(`Project: ${p.title}${p.tech ? ` ${p.tech}` : ''}`);
    for (const b of p.bullets) out.push(`  ${p.title}: ${b.text}`);
  }
  for (const g of c.skills) out.push(`Skills · ${g.label}: ${g.value}`);
  for (const e of c.education) out.push(`Education: ${e.degree} · ${e.school}`);
  for (const [label, list] of [['Soft skill', c.softSkills], ['Language', c.languages], ['Interest', c.interests]] as const)
    for (const x of list) out.push(`${label}: ${x}`);
  for (const s of c.custom) for (const e of s.entries) {
    out.push(`${s.title}: ${[e.title, e.subtitle, e.dates].filter(Boolean).join(' · ')}`);
    for (const b of e.bullets) out.push(`  ${e.title}: ${b.text}`);
  }
  return out.map((l) => l.replace(/\*\*/g, ''));
}

function Diff({ from, to }: { from: Content; to: Content }) {
  const a = lines(from);
  const b = lines(to);
  const removed = a.filter((l) => !b.includes(l));
  const added = b.filter((l) => !a.includes(l));
  if (!removed.length && !added.length) return <p className="hint">No visible text changes.</p>;
  return (
    <ul className="chat-diff">
      {removed.map((l, i) => <li key={`r${i}`} className="del">− {l}</li>)}
      {added.map((l, i) => <li key={`a${i}`} className="add">+ {l}</li>)}
    </ul>
  );
}

export function ChatPanel({ data, variant, update }: { data: CvData; variant: Variant; update: Update }) {
  const [messages, setMessages] = useState<Message[]>(load);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    save(messages);
  }, [messages]);
  // Braces: newer browsers return a Promise from scrollIntoView, which React would take for a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [messages, busy]);

  const shown = shownContent(data, variant);
  const hidden = variant.hidden;

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    const base = JSON.stringify(shown);
    const history: Message[] = [...messages, { role: 'user', text: q }];
    setMessages(history);
    setText('');
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API}chat${docQuery}`, {
        method: 'POST',
        body: JSON.stringify({
          messages: history.map((m) => ({ role: m.role, text: m.text, changes: m.changes, state: m.state })),
          content: shown,
          hidden,
          version: variant.name,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      setMessages([...history, { role: 'assistant', text: body.reply, changes: body.changes, content: body.content, base, state: '' }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const decide = (i: number, state: 'applied' | 'dismissed') => {
    const m = messages[i];
    if (state === 'applied' && m.content) {
      if (m.base !== JSON.stringify(shown) && !confirm('The CV changed since Claude made this proposal. Applying it replaces those edits. Apply anyway?')) return;
      const proposed = m.content;
      update((d) => applyProposal(d, variant.id, proposed));
    }
    setMessages(messages.map((x, j) => (j === i ? { ...x, state } : x)));
  };

  return (
    <div className="panel chat-panel">
      <h3>Chat</h3>
      <p className="hint">
        Ask Claude about this CV{data.job ? ` and the ${data.job.title} job` : ''}, or ask it to change something. It uses only facts from your profile and this chat;
        a change is applied only when you click Apply (Ctrl+Z undoes it).
      </p>
      <div className="chat-log" aria-live="polite">
        {!messages.length && (
          <div className="chat-starters">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            <div className="chat-text" dangerouslySetInnerHTML={{ __html: richToHtml(m.text) }} />
            {m.content && (
              <div className="chat-change">
                <strong>Proposed change</strong>
                <ul>{(m.changes?.length ? m.changes : ['Updated the CV']).map((c, j) => <li key={j}>{c}</li>)}</ul>
                {m.base && (
                  <details>
                    <summary>Show exactly what changes</summary>
                    <Diff from={JSON.parse(m.base)} to={m.content} />
                  </details>
                )}
                {m.state ? (
                  <p className="hint">{m.state === 'applied' ? 'Applied. Ctrl+Z undoes it.' : 'Not applied.'}</p>
                ) : (
                  <div className="chat-actions">
                    <button className="primary" onClick={() => decide(i, 'applied')}>Apply</button>
                    <button onClick={() => decide(i, 'dismissed')}>Not now</button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="rw-busy">
            <span className="spin" /> Claude is thinking… usually 10–40 seconds.
          </div>
        )}
        {error && <div className="rw-error">{error}</div>}
        <div ref={end} />
      </div>
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <textarea
          value={text}
          rows={3}
          placeholder="e.g. “Replace the projects with my payments simulator” or “What's missing for this job?”"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <div className="chat-form-row">
          {messages.length > 0 && (
            <button type="button" className="ghost" disabled={busy} onClick={() => setMessages([])}>New chat</button>
          )}
          <button className="primary" disabled={busy || !text.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}
