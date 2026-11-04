import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CvData, SectionKey } from './types';
import { CvPage } from './CvPage';
import { EditContext, editField, focusField } from './Editable';
import { API, docQuery, sectionTitle } from './model';
import { withDefaults, elemStyle, styleKey, KINDS } from './design';
import { CanvasContext, blurFields, useFreeDrag, type CanvasCtx, type Guides } from './Free';
import { Toolbar, freeOps, selectorFor, type Clip, type Sel } from './Toolbar';
import { Sidebar, normalTab, type Tab } from './Sidebar';
import { BoxHandles } from './Resize';
import { Dropdown } from './ui';
import { PARENT_KIND, asSection, caps, clearField, describe, insertBelow, moveItem, removeBuiltinSection, removeItem, toggleHidden } from './items';
import { Shortcuts } from './Shortcuts';

/** What a selection box can pick, besides sections (whole) and free elements. */
const MARQUEE_ITEMS = ['job', 'project', 'skill', 'edu', 'entry', 'photo', 'listItem', 'contact', 'bullet', 'name', 'title', 'summary']
  .map((k) => `.canvas .page [data-k=${k}]`)
  .join(', ');

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
  const data = useMemo(() => ({ ...raw, content: { ...raw.content, custom: raw.content.custom ?? [] }, design: withDefaults(raw.design) }), [raw]);
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
  /** "Deleted the job · Undo" after a change made with a shortcut or the toolbar. */
  const [notice, setNotice] = useState('');
  const [showKeys, setShowKeys] = useState(false);
  /** Several things selected at once (selection box or Shift+click); a single selection uses `sel`. */
  const [multi, setMulti] = useState<Sel[]>([]);
  const [marquee, setMarquee] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
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
  const [tab, setTabState] = useState<Tab | null>(() => (lsGet('cv.tab') === '' ? null : normalTab(lsGet('cv.tab')) ?? 'content'));
  const setTab = (t: Tab | null) => {
    setTabState(t);
    lsSet('cv.tab', t ?? '');
  };
  const rev = useRef('');
  const clip = useRef<Clip>({});
  const canvasRef = useRef<HTMLElement>(null);
  const hover = useRef<HTMLElement | null>(null);
  /** Set on pointerdown: the click only selects a text field (no caret), like Canva. Read by mousedown. */
  const selectOnly = useRef(false);

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
        d.content.custom ??= [];
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

  /** One level up from the selection: a job's role -> the job -> the section -> the column -> the page -> nothing. */
  const parentOf = useCallback((s: Sel): Sel => {
    if (s?.type !== 'el') return null;
    const node = document.querySelector<HTMLElement>(selectorFor(s.k, s.i));
    const up = node?.parentElement?.closest<HTMLElement>('[data-k]');
    return up ? { type: 'el', k: up.dataset.k!, i: up.dataset.i } : null;
  }, []);

  const flash = useCallback((text: string) => {
    setNotice(text);
    window.clearTimeout((flash as unknown as { t?: number }).t);
    (flash as unknown as { t?: number }).t = window.setTimeout(() => setNotice(''), 5000);
  }, []);

  /** Delete / duplicate / move / add below / hide the selected item (shared by keys, toolbar and menu). */
  const itemAction = useCallback(
    (what: 'remove' | 'copy' | 'new' | 'up' | 'down' | 'hide', k0: string, i0?: string) => {
      const d = dataRef.current;
      if (!d) return;
      checkpoint(); // each item action is its own undo step, however fast they follow each other
      const [k, i] = asSection(k0, i0);
      const can = caps(d, k, i);
      const vi = Math.max(0, d.variants.findIndex((v) => v.id === variantId));
      if (what === 'remove') {
        if (can.builtinSection && i) {
          update((x) => removeBuiltinSection(x, i));
          setSel(null);
          flash(`Removed ${describe(d.content, k, i)} from the page (its text stays in your profile: Content → Shown brings it back)`);
        } else if (k === 'sideCol' && can.remove) {
          update((x) => void (x.design.layout.sidebar = 'none'));
          setSel(null);
          flash('Removed the sidebar: its sections now sit under the main column (Layout → Sidebar brings it back)');
        } else if (can.clear) {
          update((x) => void clearField(x, x.variants[vi], k, i));
          flash(`Cleared the ${(KINDS[k]?.label ?? 'text').toLowerCase()}`);
        } else if (can.remove) {
          const label = describe(d.content, k, i);
          update((x) => void removeItem(x, k, i));
          setSel(null);
          flash(
            k === 'photo'
              ? 'Removed the photo and its spot (Content → Add a photo brings it back)'
              : k === 'jobLogo'
                ? 'Removed the logo spot for this job (select the job: “+ Logo” brings it back)'
                : `Deleted ${label}`,
          );
        } else {
          flash(
            k === 'page' || k === 'mainCol'
              ? `The ${k === 'page' ? 'page' : 'main column'} itself can't be deleted: select what's on it (drag a box to pick several)`
              : 'Nothing to delete here',
          );
        }
      } else if (what === 'hide') {
        if (!i) return;
        update((x) => toggleHidden(x.variants[vi], i));
        setSel(null);
        flash(`Hid ${describe(d.content, k, i)} in this version (Content → Shown brings it back)`);
      } else if (what === 'copy' || what === 'new') {
        if (!can.addBelow) return;
        let id: string | undefined;
        update((x) => void (id = insertBelow(x, k, i, what)));
        if (id) setSel({ type: 'el', k, i: id });
        if (what === 'copy') flash(`Duplicated the ${can.name}`);
      } else {
        let id: string | undefined;
        update((x) => void (id = moveItem(x, k, i, what === 'up' ? -1 : 1)));
        setSel({ type: 'el', k, i: id });
      }
    },
    [update, setSel, variantId, flash, checkpoint],
  );

  /** Delete or hide everything in a multiple selection (one undo step). */
  const groupAction = useCallback(
    (what: 'remove' | 'hide', targets: Sel[]) => {
      const d = dataRef.current;
      if (!d || !targets.length) return;
      checkpoint();
      const vi = Math.max(0, d.variants.findIndex((v) => v.id === variantId));
      // String lists are addressed by index: remove from the end so earlier indexes stay right.
      const order = (t: Sel) => (t?.type === 'el' && /^(c|soft|lang|interest)\d+$/.test(t.i ?? '') ? -Number(/\d+$/.exec(t.i!)![0]) : 0);
      const sorted = [...targets].sort((a, b) => order(a) - order(b));
      let n = 0;
      update((x) => {
        for (const t of sorted) {
          if (!t) continue;
          if (t.type === 'free') {
            if (what === 'remove') x.design.free = x.design.free.filter((f) => f.id !== t.id);
            else toggleHidden(x.variants[vi], t.id);
            n++;
            continue;
          }
          const [k, i] = asSection(t.k, t.i);
          const can = caps(x, k, i);
          if (what === 'hide') {
            if (i && ['job', 'project', 'skill', 'bullet', 'entry', 'section'].includes(k) && !x.variants[vi].hidden.includes(i)) {
              x.variants[vi].hidden.push(i);
              n++;
            }
          } else if (can.builtinSection && i) {
            removeBuiltinSection(x, i);
            n++;
          } else if (can.clear) {
            if (clearField(x, x.variants[vi], k, i)) n++;
          } else if (removeItem(x, k, i)) n++;
        }
      });
      setMulti([]);
      setSel(null);
      flash(what === 'remove' ? `Deleted ${n} item${n === 1 ? '' : 's'}` : `Hid ${n} item${n === 1 ? '' : 's'} in this version`);
    },
    [update, setSel, variantId, flash, checkpoint],
  );

  /** Keys for a selected item; returns true when the key was used. */
  const itemKey = useCallback(
    (e: KeyboardEvent, k: string, i?: string): boolean => {
      const d = dataRef.current;
      if (!d) return false;
      const mod = e.ctrlKey || e.metaKey;
      const can = caps(d, k, i);
      if ((e.key === 'Delete' || e.key === 'Backspace') && !mod && (can.remove || can.item)) return (itemAction('remove', k, i), true);
      if (!can.item) return false;
      if (mod && !e.shiftKey && e.key.toLowerCase() === 'd' && can.duplicate) return (itemAction('copy', k, i), true);
      if (mod && e.key === 'Enter' && can.addBelow) return (itemAction('new', k, i), true);
      if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && can.move) return (itemAction(e.key === 'ArrowUp' ? 'up' : 'down', k, i), true);
      // (not bullets: typing "h" on a selected bullet starts editing it)
      if (!mod && !e.altKey && e.key.toLowerCase() === 'h' && i && ['job', 'project', 'skill', 'entry', 'section'].includes(k)) return (itemAction('hide', k, i), true);
      return false;
    },
    [itemAction],
  );

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
        setMenu(null);
        setShowKeys(false);
        if (multi.length) setMulti([]);
        else setSel(parentOf(sel));
        return;
      }
      if (e.key === '?' && !mod) {
        setShowKeys((v) => !v);
        return;
      }
      if (mod && !e.altKey && key === 'v' && clip.current.el) {
        e.preventDefault();
        ops.paste(clip.current.el);
        return;
      }
      if (multi.length > 1) {
        if ((e.key === 'Delete' || e.key === 'Backspace') && !mod) {
          e.preventDefault();
          groupAction('remove', multi);
        } else if (key === 'h' && !mod) {
          groupAction('hide', multi);
        } else if (e.key.startsWith('Arrow') && !mod && !e.altKey) {
          e.preventDefault();
          const step = e.shiftKey ? 5 : 0.5;
          const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key] ?? [0, 0];
          const ids = new Set(multi.flatMap((t) => (t?.type === 'free' ? [t.id] : [])));
          update((x) => {
            for (const f of x.design.free) if (ids.has(f.id) && !f.locked) {
              f.x = Math.round((f.x + dx) * 100) / 100;
              f.y = Math.round((f.y + dy) * 100) / 100;
            }
          });
        }
        return;
      }
      if (!sel) return;
      if (sel.type === 'el') {
        if (itemKey(e, sel.k, sel.i)) {
          e.preventDefault();
          return;
        }
        // Enter, F2 or just typing starts editing a selected text field (the typed key goes into it).
        const field = document.querySelector<HTMLElement>(`${selectorFor(sel.k, sel.i)}.editable`);
        if (field && !mod && !e.altKey && (e.key === 'Enter' || e.key === 'F2' || e.key.length === 1)) {
          if (e.key.length !== 1) e.preventDefault();
          editField(field);
          return;
        }
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
  }, [undo, redo, sel, ops, update, itemKey, parentOf, multi, groupAction]);

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

  /** Move several free elements together. */
  const groupDrag = (e: React.PointerEvent, ids: string[]) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const page = document.querySelector<HTMLElement>('.canvas .page');
    const d = dataRef.current;
    if (!page || !d) return;
    const mm = 210 / page.getBoundingClientRect().width;
    const starts = new Map(d.design.free.filter((f) => ids.includes(f.id) && !f.locked).map((f) => [f.id, { x: f.x, y: f.y }]));
    const sx = e.clientX;
    const sy = e.clientY;
    let moved = false;
    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - sx) * mm;
      const dy = (ev.clientY - sy) * mm;
      if (!moved && Math.hypot(dx, dy) < 1.5) return;
      if (!moved) checkpoint();
      moved = true;
      update((x) => {
        for (const f of x.design.free) {
          const s0 = starts.get(f.id);
          if (s0) {
            f.x = Math.round((s0.x + dx) * 100) / 100;
            f.y = Math.round((s0.y + dy) * 100) / 100;
          }
        }
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (moved) checkpoint();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Outline everything in a multiple selection.
  useLayoutEffect(() => {
    document.querySelectorAll('.multi-sel').forEach((n) => n.classList.remove('multi-sel'));
    for (const t of multi) {
      const n = t?.type === 'free' ? document.querySelector(`.canvas [data-free="${t.id}"]`) : t ? document.querySelector(selectorFor(t.k, t.i)) : null;
      n?.classList.add('multi-sel');
    }
  });

  const editCtx = useMemo(() => ({ editable: true, focus: setPendingFocus }), []);
  const canvasCtx: CanvasCtx = {
    active: true,
    selected: sel?.type === 'free' ? sel.id : null,
    editing,
    guides,
    startDrag: (e, el, mode) => {
      const ids = multi.flatMap((t) => (t?.type === 'free' ? [t.id] : []));
      if (mode === 'move' && ids.length > 1 && ids.includes(el.id)) groupDrag(e, ids);
      else startDrag(e, el, mode);
    },
    select: (id, shift) => {
      if (editing && editing !== id) setEditing(null);
      if (shift) return toggleMulti({ type: 'free', id });
      if (multi.some((t) => t?.type === 'free' && t.id === id)) return; // keep the group to drag it
      setMulti([]);
      setSel({ type: 'free', id });
    },
    multi: multi.flatMap((t) => (t?.type === 'free' ? [t.id] : [])),
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

  /** Shift+click picks whole items: a job's role picks the job. */
  const pickItem = (target: HTMLElement): Sel => {
    let s = pick(target);
    while (s?.type === 'el' && PARENT_KIND[s.k]) s = parentOf(s);
    return s;
  };
  const sameSel = (a: Sel, b: Sel) =>
    !!a && !!b && a.type === b.type && (a.type === 'free' ? a.id === (b as { id: string }).id : a.k === (b as { k: string }).k && a.i === (b as { i?: string }).i);
  const toggleMulti = (t: Sel) => {
    if (!t) return;
    const base = multi.length ? multi : sel ? [sel] : [];
    const next = base.some((x) => sameSel(x, t)) ? base.filter((x) => !sameSel(x, t)) : [...base, t];
    if (next.length === 1) {
      setMulti([]);
      setSel(next[0]);
    } else {
      setMulti(next);
      setSel(null);
    }
  };

  /** Drag from an empty spot: a selection box; on release everything it touches is selected (sections only when
   *  the box holds all of them). A click without dragging selects as usual. */
  const startMarquee = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const sx = e.clientX;
    const sy = e.clientY;
    const add = e.shiftKey;
    let dragging = false;
    const onMove = (ev: PointerEvent) => {
      if (!dragging && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) return;
      if (!dragging) {
        dragging = true;
        blurFields();
        window.getSelection()?.removeAllRanges();
      }
      ev.preventDefault();
      setMarquee({ x: Math.min(sx, ev.clientX), y: Math.min(sy, ev.clientY), w: Math.abs(ev.clientX - sx), h: Math.abs(ev.clientY - sy) });
    };
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      setMarquee(null);
      if (!dragging) return;
      const box = { l: Math.min(sx, ev.clientX), t: Math.min(sy, ev.clientY), r: Math.max(sx, ev.clientX), b: Math.max(sy, ev.clientY) };
      const hits = (n: Element, whole = false) => {
        const r = n.getBoundingClientRect();
        if (!r.width && !r.height) return false;
        return whole
          ? r.left >= box.l && r.right <= box.r && r.top >= box.t && r.bottom <= box.b
          : r.left < box.r && r.right > box.l && r.top < box.b && r.bottom > box.t;
      };
      const found: Sel[] = [];
      const sections = [...document.querySelectorAll<HTMLElement>('.canvas .page [data-k=section]')].filter((n) => hits(n, true));
      for (const n of sections) found.push({ type: 'el', k: 'section', i: n.dataset.i });
      for (const n of document.querySelectorAll<HTMLElement>(MARQUEE_ITEMS)) {
        if (n.closest('.is-hidden') || sections.some((s) => s.contains(n)) || !hits(n)) continue;
        if (n.dataset.k === 'bullet' && n.closest('[data-k=job], [data-k=project], [data-k=entry]') && hits(n.closest('[data-k=job], [data-k=project], [data-k=entry]')!)) {
          // a bullet counts on its own only when its job/project/entry isn't picked
          const owner = n.closest<HTMLElement>('[data-k=job], [data-k=project], [data-k=entry]')!;
          if (found.some((f) => f?.type === 'el' && f.i === owner.dataset.i)) continue;
        }
        if (n.dataset.k === 'bullet' && found.some((f) => f?.type === 'el' && n.closest(`[data-i="${f.i}"]`) && f.k !== 'bullet')) continue;
        found.push({ type: 'el', k: n.dataset.k!, i: n.dataset.i });
      }
      for (const n of document.querySelectorAll<HTMLElement>('.canvas .page .free[data-free]')) if (hits(n)) found.push({ type: 'free', id: n.dataset.free! });
      // drop bullets whose job/project/entry was picked after them
      const picked = found.filter((f) => !(f?.type === 'el' && f.k === 'bullet' && found.some((o) => o?.type === 'el' && o.k !== 'bullet' && o.i && document.querySelector(`.canvas .page [data-i="${o.i}"] [data-k=bullet][data-i="${f.i}"]`))));
      const all = add ? [...(multi.length ? multi : sel ? [sel] : []), ...picked.filter((p) => !(multi.length ? multi : sel ? [sel] : []).some((x) => sameSel(x, p)))] : picked;
      if (all.length === 1) {
        setMulti([]);
        setSel(all[0]);
      } else {
        setMulti(all);
        setSel(null);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const onPagePointerDown = (e: React.PointerEvent) => {
    const t = e.target as HTMLElement;
    if (t.closest('.free, .selection, .tools, .add-btn, .reveal')) return;
    if (editing) setEditing(null);
    if (e.shiftKey) {
      e.preventDefault();
      toggleMulti(pickItem(t));
      return;
    }
    setMulti([]);
    const next = pick(t);
    // A text field you aren't typing in yet: the first click selects it (Del deletes it), a second click or
    // typing starts editing.
    const field = t.closest<HTMLElement>('.editable');
    selectOnly.current = !!field && document.activeElement !== field && !sameSel(sel, next);
    if (selectOnly.current) blurFields();
    setSel(next);
    // Blank spots (not text, images or buttons) start a selection box when dragged.
    if (!t.closest('.editable, img, button, a')) startMarquee(e);
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
      const label = k === 'section' ? `${sectionTitle(data.content, node.dataset.i as SectionKey)} section` : KINDS[k]?.label ?? k;
      crumbs.unshift({ label, sel: { type: 'el', k, i: node.dataset.i } });
      node = node.parentElement?.closest<HTMLElement>('[data-k]') ?? null;
    }
  } else if (sel?.type === 'free') {
    crumbs.push({ label: 'Page', sel: { type: 'el', k: 'page' } }, { label: 'Element', sel });
  }

  const freeSel = sel?.type === 'free' ? data.design.free.find((f) => f.id === sel.id) : undefined;
  const itemMenu = (k0: string, i0?: string): [string, () => void, boolean?][] => {
    const [k, i] = asSection(k0, i0);
    const can = caps(data, k, i);
    const out: [string, () => void, boolean?][] = [];
    const parentK = PARENT_KIND[k];
    if (parentK) {
      const up = parentOf({ type: 'el', k, i });
      if (up) out.push([`Select the whole ${caps(data, parentK, i).name}  (Esc)`, () => setSel(up)]);
    }
    if (can.addBelow) out.push([`Add a ${can.name} below  (Ctrl+Enter)`, () => itemAction('new', k, i)]);
    if (can.duplicate) out.push(['Duplicate  (Ctrl+D)', () => itemAction('copy', k, i)]);
    if (can.move) out.push(['Move up  (Alt+↑)', () => itemAction('up', k, i)], ['Move down  (Alt+↓)', () => itemAction('down', k, i)]);
    if (i && ['job', 'project', 'skill', 'bullet', 'entry', 'section'].includes(k)) out.push(['Hide in this version  (H)', () => itemAction('hide', k, i)]);
    if (can.remove) {
      const label = can.clear ? 'Clear this text' : can.builtinSection ? 'Remove the section from the page' : k === 'sideCol' ? 'Remove the sidebar' : k === 'photo' ? 'Remove the photo' : 'Delete';
      out.push([`${label}  (Del)`, () => itemAction('remove', k, i)]);
    }
    return out;
  };
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
          ...itemMenu(sel.k, sel.i),
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
              <Dropdown className="version-select" title="The version you are editing" value={variant.id} onChange={select} options={data.variants.map((v) => ({ value: v.id, label: v.name }))} />
            )}
            <button className="ghost" onClick={undo} disabled={!past.current.length} title="Undo (Ctrl+Z)">
              ↶ Undo
            </button>
            <button className="ghost" onClick={redo} disabled={!future.current.length} title="Redo (Ctrl+Shift+Z)">
              ↷ Redo
            </button>
            <button className="ghost" onClick={() => setShowKeys(true)} title="Keyboard shortcuts (?)">
              ⌨ Shortcuts
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

          <Sidebar data={data} variant={variant} update={update} select={select} sel={sel} setSel={setSel} upload={upload} tab={tab} setTab={setTab} itemAction={itemAction} />

          <div className="work">
            {multi.length > 1 ? (
              <div className="toolbar group-bar">
                <span className="tb-kind">{multi.length} selected</span>
                <span className="hint">Shift+click adds or removes one · drag an empty spot to select with a box</span>
                <span className="spacer" />
                <button onClick={() => groupAction('hide', multi)} title="Hide them in this version (H)">Hide in this version</button>
                <button className="danger" onClick={() => groupAction('remove', multi)} title="Delete them all (Del)">Delete all</button>
                <button className="ghost" onClick={() => setMulti([])} title="Esc">Clear selection</button>
              </div>
            ) : (
              <Toolbar data={data} variant={variant} sel={sel} setSel={setSel} update={update} clip={clip} upload={upload} scope={scope} setScope={setScope} itemAction={itemAction} parentOf={parentOf} />
            )}
            <main
              className="canvas"
              ref={canvasRef}
              onPointerDown={(e) => {
                if (!(e.target as HTMLElement).closest('.page')) {
                  blurFields();
                  setSel(null);
                  if (!e.shiftKey) setMulti([]);
                  setEditing(null);
                  startMarquee(e);
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
                onMouseDown={(e) => {
                  if (e.shiftKey || (selectOnly.current && e.detail < 2)) e.preventDefault(); // a double-click edits right away
                  selectOnly.current = false;
                }}
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
          {marquee && <div className="marquee" style={{ left: marquee.x, top: marquee.y, width: marquee.w, height: marquee.h }} />}
          {busy && <div className="busy">{busy}</div>}
          {notice && (
            <div className="notice" role="status">
              {notice}
              <button
                onClick={() => {
                  undo();
                  setNotice('');
                }}
              >
                Undo
              </button>
            </div>
          )}
          {showKeys && <Shortcuts onClose={() => setShowKeys(false)} />}
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
