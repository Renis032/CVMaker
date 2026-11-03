import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CvData } from './types';
import { CvPage } from './CvPage';
import { EditContext, focusField } from './Editable';
import { API, docQuery } from './model';
import { withDefaults, elemStyle, styleKey, KINDS } from './design';
import { CanvasContext, blurFields, useFreeDrag, type CanvasCtx, type Guides } from './Free';
import { Toolbar, freeOps, selectorFor, type Clip, type Sel } from './Toolbar';
import { Sidebar, type Tab } from './Sidebar';
import { BoxHandles } from './Resize';

declare global {
  interface Window { __CV_READY__?: boolean; __CV_PRINT__?: CvData }
}

async function upload(file: File): Promise<string> {
  const res = await fetch(`${API}upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: await file.arrayBuffer() });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error);
  return body.path as string;
}

function lsGet(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function lsSet(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

/** The page CVMaker prints to PDF: the data is embedded in the HTML file (window.__CV_PRINT__). */
export function PrintView({ data: raw }: { data: CvData }) {
  const data = useMemo(() => ({ ...raw, design: withDefaults(raw.design) }), [raw]);
  const variant = data.variants[0];
  useEffect(() => {
    const imgs = [...document.images].map((i) => (i.complete ? Promise.resolve() : new Promise((r) => (i.onload = i.onerror = r))));
    Promise.all([document.fonts.ready, ...imgs]).then(() => (window.__CV_READY__ = true));
  }, [variant]);
  return (
    <EditContext.Provider value={{ editable: false, focus: () => {} }}>
      <div className="print-root">
        <CvPage data={data} variant={variant} update={() => {}} upload={upload} />
      </div>
    </EditContext.Provider>
  );
}

export default function App() {
  const [data, setData] = useState<CvData | null>(null);
  const [error, setError] = useState('');
  const [variantId, setVariantId] = useState('');
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved');
  const [overflow, setOverflow] = useState(0);
  const [zoom, setZoom] = useState(() => Number(lsGet('cv.zoom')) || 1);
  const [busy, setBusy] = useState('');
  const [toast, setToast] = useState('');
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const [conflict, setConflict] = useState('');
  const [sel, setSelState] = useState<Sel>(null);
  const [scope, setScope] = useState<'all' | 'one'>('all');
  const setSel = useCallback((s: Sel) => {
    setSelState((old) => {
      const same = old && s && old.type === s.type && (old.type === 'free' ? old.id === (s as { id: string }).id : old.k === (s as { k: string }).k && old.i === (s as { i?: string }).i);
      if (!same) setScope('all');
      return s;
    });
  }, []);
  const [editing, setEditing] = useState<string | null>(null);
  const [guides, setGuides] = useState<Guides | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [tab, setTabState] = useState<Tab | null>(() => (lsGet('cv.tab') as Tab | null) || 'design');
  const setTab = (t: Tab | null) => {
    setTabState(t);
    lsSet('cv.tab', t ?? '');
  };
  const rev = useRef('');
  const clip = useRef<Clip>({});
  const canvasRef = useRef<HTMLElement>(null);
  const hover = useRef<HTMLElement | null>(null);

  const past = useRef<CvData[]>([]);
  const future = useRef<CvData[]>([]);
  const lastPush = useRef(0);
  const dataRef = useRef<CvData | null>(null);
  const loaded = useRef(false);
  dataRef.current = data;

  useEffect(() => {
    fetch(`${API}data${docQuery}`)
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`))))
      .then((d: CvData) => {
        rev.current = d.rev ?? '';
        d.design = withDefaults(d.design);
        setData(d);
        const remembered = lsGet('cv.variant');
        setVariantId(d.variants.some((v) => v.id === remembered) ? remembered! : d.variants[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const update = useCallback((fn: (d: CvData) => void) => {
    const prev = dataRef.current;
    if (!prev) return;
    const next = structuredClone(prev);
    fn(next);
    // Coalesce rapid edits (typing in inputs, dragging sliders) into one undo step.
    const now = Date.now();
    if (now - lastPush.current > 600) past.current.push(prev);
    lastPush.current = now;
    if (past.current.length > 300) past.current.shift();
    future.current = [];
    dataRef.current = next;
    setData(next);
  }, []);
  /** The next change starts a new undo step (a drag is one step however long it takes). */
  const checkpoint = useCallback(() => void (lastPush.current = 0), []);

  const undo = useCallback(() => {
    const prev = past.current.pop();
    if (!prev || !dataRef.current) return;
    future.current.push(dataRef.current);
    dataRef.current = prev;
    lastPush.current = 0;
    setData(prev);
  }, []);

  const redo = useCallback(() => {
    const next = future.current.pop();
    if (!next || !dataRef.current) return;
    past.current.push(dataRef.current);
    dataRef.current = next;
    lastPush.current = 0;
    setData(next);
  }, []);

  const conflictRef = useRef(false);
  const save = useCallback(async () => {
    if (!dataRef.current || conflictRef.current) return;
    setSaveState('saving');
    try {
      const res = await fetch(`${API}data${docQuery}`, { method: 'POST', body: JSON.stringify({ ...dataRef.current, rev: rev.current }) });
      const body = await res.json().catch(() => ({}));
      if (res.status === 409) {
        conflictRef.current = true;
        setConflict(body.error || 'Changed somewhere else. Reload to continue.');
      }
      if (res.ok) rev.current = body.rev ?? rev.current;
      setSaveState(res.ok ? 'saved' : 'error');
    } catch {
      setSaveState('error');
    }
  }, []);

  // Autosave shortly after each change.
  useEffect(() => {
    if (!data) return;
    if (!loaded.current) {
      loaded.current = true;
      return;
    }
    setSaveState('saving');
    const t = setTimeout(save, 500);
    return () => clearTimeout(t);
  }, [data, save]);

  const ops = useMemo(() => freeOps(update, setSel), [update]);
  const startDrag = useFreeDrag(() => dataRef.current, update, checkpoint, setGuides);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const mod = e.ctrlKey || e.metaKey;
      if (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return;
      const key = e.key.toLowerCase();
      if (mod && key === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (mod && key === 'y') {
        e.preventDefault();
        redo();
        return;
      }
      const d = dataRef.current;
      if (!d) return;
      if (e.key === 'Escape') {
        setSel(null);
        setMenu(null);
        return;
      }
      if (mod && !e.altKey && key === 'v' && clip.current.el) {
        e.preventDefault();
        ops.paste(clip.current.el);
        return;
      }
      if (!sel) return;
      if (sel.type === 'el') {
        if (mod && e.altKey && key === 'c') clip.current.style = elemStyle(d.design, sel.k, sel.i);
        if (mod && e.altKey && key === 'v' && clip.current.style) {
          const k = sel.k;
          update((x) => void (x.design.elements[k] = { ...(x.design.elements[k] ?? {}), ...clip.current.style }));
        }
        return;
      }
      const el = d.design.free.find((f) => f.id === sel.id);
      if (!el) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        ops.remove(el.id);
      } else if (mod && key === 'd') {
        e.preventDefault();
        ops.duplicate(el.id);
      } else if (mod && !e.altKey && key === 'c') {
        clip.current.el = structuredClone(el);
      } else if (mod && key === 'x') {
        clip.current.el = structuredClone(el);
        ops.remove(el.id);
      } else if (mod && e.altKey && key === 'c') {
        clip.current.style = el.style;
      } else if (mod && e.altKey && key === 'v' && clip.current.style) {
        ops.set(el.id, (f) => void (f.style = { ...(f.style ?? {}), ...clip.current.style }));
      } else if (mod && (e.key === ']' || e.key === '[')) {
        e.preventDefault();
        ops.layer(el.id, e.key === ']' ? (e.shiftKey ? 'front' : 'forward') : e.shiftKey ? 'back' : 'backward');
      } else if (e.key === 'Enter' && el.type === 'text') {
        e.preventDefault();
        setEditing(el.id);
      } else if (e.key.startsWith('Arrow') && !el.locked) {
        e.preventDefault();
        const step = e.shiftKey ? 5 : 0.5;
        const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key] ?? [0, 0];
        ops.set(el.id, (f) => {
          f.x = Math.round((f.x + dx) * 100) / 100;
          f.y = Math.round((f.y + dy) * 100) / 100;
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo, sel, ops, update]);

  useEffect(() => {
    if (!pendingFocus) return;
    focusField(pendingFocus);
    setPendingFocus(null);
  }, [pendingFocus, data]);

  // Start typing in a text box as soon as it is in edit mode.
  useEffect(() => {
    if (editing) requestAnimationFrame(() => focusField(`free:${editing}`));
  }, [editing]);

  // Outline the selected element on the page.
  useLayoutEffect(() => {
    document.querySelectorAll('.canvas .sel').forEach((n) => n.classList.remove('sel'));
    if (sel?.type === 'el') document.querySelector(selectorFor(sel.k, sel.i))?.classList.add('sel');
  });

  useEffect(() => {
    if (!menu) return;
    const close = (e: PointerEvent) => !(e.target as HTMLElement).closest('.ctx-menu') && setMenu(null);
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menu]);

  const select = (id: string) => {
    setVariantId(id);
    lsSet('cv.variant', id);
  };

  const editCtx = useMemo(() => ({ editable: true, focus: setPendingFocus }), []);
  const canvasCtx: CanvasCtx = {
    active: true,
    selected: sel?.type === 'free' ? sel.id : null,
    editing,
    guides,
    startDrag,
    select: (id) => {
      if (editing && editing !== id) setEditing(null);
      setSel({ type: 'free', id });
    },
    startEdit: (id) => {
      setSel({ type: 'free', id });
      setEditing(id);
    },
    endEdit: () => setEditing(null),
  };

  const setZoomTo = (z: number) => {
    const v = Math.min(3, Math.max(0.25, Math.round(z * 100) / 100));
    setZoom(v);
    lsSet('cv.zoom', String(v));
  };
  const fit = () => {
    const c = canvasRef.current;
    if (!c) return;
    const page = { w: 210 / (25.4 / 96), h: 297 / (25.4 / 96) };
    setZoomTo(Math.min((c.clientWidth - 64) / page.w, (c.clientHeight - 64) / page.h));
  };

  /** Commit the field being edited and write everything to disk before exporting. */
  const flush = async () => {
    (document.activeElement as HTMLElement | null)?.blur();
    await new Promise((r) => setTimeout(r, 30));
    await save();
  };

  const exportPdfs = async (ids?: string[]) => {
    setBusy(ids ? 'Exporting PDF…' : 'Exporting all versions…');
    try {
      await flush();
      const res = await fetch(`${API}export${docQuery}`, { method: 'POST', body: JSON.stringify({ ids }) });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error);
      if (ids) window.open(`${API}file?name=${encodeURIComponent(body.files[0])}${docQuery.replace('?', '&')}`, '_blank');
      setToast(`Saved to ${body.dir}/\n${body.files.join('\n')}${body.pages > 1 ? `\n\nNote: ${body.pages} pages.` : ''}`);
    } catch (e) {
      setToast(`Export failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy('');
    }
  };

  if (error) return <div className="loading">Could not load the CV: {error} <a href="/" target="_top">Back to CVMaker</a></div>;
  if (!data) return <div className="loading">Loading…</div>;
  const isCv = data.mode === 'cv';

  const variant = data.variants.find((v) => v.id === variantId) ?? data.variants[0];

  /** What a click on the CV page selects: the innermost element that has a name (data-k). */
  const pick = (target: HTMLElement): Sel => {
    const node = target.closest<HTMLElement>('[data-k]');
    if (!node || !node.closest('.page')) return null;
    return { type: 'el', k: node.dataset.k!, i: node.dataset.i };
  };

  const onPagePointerDown = (e: React.PointerEvent) => {
    const t = e.target as HTMLElement;
    if (t.closest('.free, .selection, .tools, .add-btn, .reveal')) return;
    if (editing) setEditing(null);
    setSel(pick(t));
  };

  const onPageOver = (e: React.PointerEvent) => {
    const t = e.target as HTMLElement;
    const node = t.closest('.free') ? null : t.closest<HTMLElement>('[data-k]');
    if (node === hover.current) return;
    hover.current?.classList.remove('hov');
    hover.current = node;
    node?.classList.add('hov');
  };

  // The path from the page down to the selected element, for the breadcrumb.
  const crumbs: { label: string; sel: Sel }[] = [];
  if (sel?.type === 'el') {
    let node = document.querySelector<HTMLElement>(selectorFor(sel.k, sel.i));
    while (node) {
      const k = node.dataset.k!;
      const label = k === 'section' ? `${data.content.headings[node.dataset.i as keyof typeof data.content.headings] ?? 'Section'} section` : KINDS[k]?.label ?? k;
      crumbs.unshift({ label, sel: { type: 'el', k, i: node.dataset.i } });
      node = node.parentElement?.closest<HTMLElement>('[data-k]') ?? null;
    }
  } else if (sel?.type === 'free') {
    crumbs.push({ label: 'Page', sel: { type: 'el', k: 'page' } }, { label: 'Element', sel });
  }

  const freeSel = sel?.type === 'free' ? data.design.free.find((f) => f.id === sel.id) : undefined;
  const menuItems: [string, () => void, boolean?][] = freeSel
    ? [
        ['Copy', () => void (clip.current.el = structuredClone(freeSel))],
        ['Paste', () => clip.current.el && ops.paste(clip.current.el), !clip.current.el],
        ['Duplicate', () => ops.duplicate(freeSel.id)],
        ['Delete', () => ops.remove(freeSel.id)],
        ['Bring forward', () => ops.layer(freeSel.id, 'forward')],
        ['Send backward', () => ops.layer(freeSel.id, 'backward')],
        ['Bring to front', () => ops.layer(freeSel.id, 'front')],
        ['Send to back', () => ops.layer(freeSel.id, 'back')],
        [freeSel.behind ? 'Put in front of the text' : 'Put behind the text', () => ops.set(freeSel.id, (f) => void (f.behind = !f.behind))],
        [freeSel.locked ? 'Unlock' : 'Lock', () => ops.set(freeSel.id, (f) => void (f.locked = !f.locked))],
        ['Copy style', () => void (clip.current.style = freeSel.style)],
        ['Paste style', () => ops.set(freeSel.id, (f) => void (f.style = { ...(f.style ?? {}), ...clip.current.style })), !clip.current.style || freeSel.type !== 'text'],
      ]
    : sel?.type === 'el'
      ? [
          ['Copy style', () => void (clip.current.style = elemStyle(data.design, sel.k, sel.i))],
          ['Paste style to all like it', () => update((d) => void (d.design.elements[sel.k] = { ...(d.design.elements[sel.k] ?? {}), ...clip.current.style })), !clip.current.style],
          ['Paste style to this one', () => update((d) => void (d.design.elements[styleKey(sel.k, sel.i)] = { ...(d.design.elements[styleKey(sel.k, sel.i)] ?? {}), ...clip.current.style })), !clip.current.style || !sel.i],
          ['Reset this style', () => update((d) => {
            delete d.design.elements[sel.k];
            if (sel.i) delete d.design.elements[styleKey(sel.k, sel.i)];
          })],
          ['Paste element', () => clip.current.el && ops.paste(clip.current.el), !clip.current.el],
        ]
      : [['Paste element', () => clip.current.el && ops.paste(clip.current.el), !clip.current.el]];

  return (
    <EditContext.Provider value={editCtx}>
      <CanvasContext.Provider value={canvasCtx}>
        <div className={`app ${tab ? 'with-drawer' : ''}`}>
          <div className="topbar">
            {isCv && (
              <a className="back" href={data.back || '/'} target="_top" title="Back to this CV's page">
                ← Back to the CV
              </a>
            )}
            <strong className="brand">{data.label || 'CV editor'}</strong>
            {!isCv && (
              <select className="version-select" value={variant.id} onChange={(e) => select(e.target.value)} title="The version you are editing">
                {data.variants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
            <button className="ghost" onClick={undo} disabled={!past.current.length} title="Undo (Ctrl+Z)">
              ↶ Undo
            </button>
            <button className="ghost" onClick={redo} disabled={!future.current.length} title="Redo (Ctrl+Shift+Z)">
              ↷ Redo
            </button>
            <span className={`save-state ${saveState}`}>{saveState === 'saved' ? 'All changes saved' : saveState === 'saving' ? 'Saving…' : 'Save failed'}</span>
            <span className="spacer" />
            <button className="primary" disabled={!!busy || !!conflict} onClick={() => exportPdfs([variant.id])}>
              {isCv ? 'Update the PDF' : 'Export this PDF'}
            </button>
            {!isCv && (
              <button className="primary" disabled={!!busy || !!conflict} onClick={() => exportPdfs()}>
                Export all ({data.variants.length})
              </button>
            )}
          </div>

          <Sidebar data={data} variant={variant} update={update} select={select} sel={sel} setSel={setSel} upload={upload} tab={tab} setTab={setTab} />

          <div className="work">
            <Toolbar data={data} variant={variant} sel={sel} setSel={setSel} update={update} clip={clip} upload={upload} scope={scope} setScope={setScope} />
            <main
              className="canvas"
              ref={canvasRef}
              onPointerDown={(e) => {
                if (!(e.target as HTMLElement).closest('.page')) {
                  blurFields();
                  setSel(null);
                  setEditing(null);
                }
              }}
            >
              {conflict && (
                <div className="overflow-warning">
                  {conflict} <button onClick={() => location.reload()}>Reload</button>
                </div>
              )}
              <div
                className="page-holder"
                style={{ zoom }}
                onPointerDown={onPagePointerDown}
                onPointerOver={onPageOver}
                onPointerLeave={() => {
                  hover.current?.classList.remove('hov');
                  hover.current = null;
                }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  const t = e.target as HTMLElement;
                  if (!t.closest('.free, .selection')) setSel(pick(t));
                  setMenu({ x: e.clientX, y: e.clientY });
                }}
              >
                <CvPage data={data} variant={variant} update={update} upload={upload} onOverflow={(mm) => setOverflow((o) => (Math.abs(o - mm) > 0.5 ? mm : o))} />
                {sel?.type === 'el' && (
                  <BoxHandles
                    k={sel.k}
                    i={sel.i}
                    styleKey={scope === 'one' && sel.i && KINDS[sel.k]?.plural ? styleKey(sel.k, sel.i) : sel.k}
                    data={data}
                    update={update}
                    checkpoint={checkpoint}
                  />
                )}
              </div>
            </main>
            <footer className="bottombar">
              <span className="crumbs">
                {crumbs.length ? (
                  crumbs.map((c, i) => (
                    <span key={i}>
                      {i > 0 && <span className="crumb-sep">›</span>}
                      <button className={i === crumbs.length - 1 ? 'on' : ''} onClick={() => setSel(c.sel)}>
                        {c.label}
                      </button>
                    </span>
                  ))
                ) : (
                  <span className="muted">Nothing selected</span>
                )}
              </span>
              {overflow > 0 && <span className="overflow">Runs {overflow.toFixed(0)} mm past the page: trim text or reduce the text size or spacing.</span>}
              <span className="spacer" />
              <span className="zoom">
                <button onClick={() => setZoomTo(zoom - 0.1)} title="Zoom out">−</button>
                <input type="range" min={0.25} max={3} step={0.05} value={zoom} onChange={(e) => setZoomTo(Number(e.target.value))} />
                <button onClick={() => setZoomTo(zoom + 0.1)} title="Zoom in">+</button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={fit} title="Fit the page in the window">Fit</button>
              </span>
            </footer>
          </div>

          {menu && (
            <div className="ctx-menu" style={{ left: menu.x, top: menu.y }}>
              {menuItems.map(([label, run, disabled]) => (
                <button
                  key={label}
                  disabled={disabled}
                  onClick={() => {
                    run();
                    setMenu(null);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {busy && <div className="busy">{busy}</div>}
          {toast && (
            <div className="toast" onClick={() => setToast('')}>
              <pre>{toast}</pre>
              <small>Click to dismiss</small>
            </div>
          )}
        </div>
      </CanvasContext.Provider>
    </EditContext.Provider>
  );
}
