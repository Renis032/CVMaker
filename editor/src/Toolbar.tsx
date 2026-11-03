// The bar above the page: controls for whatever is selected, like Canva's top toolbar.
import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import type { CvData, ElemStyle, FreeEl, SectionKey, Variant } from './types';
import { DEFAULT_COLORS, FONTS, KINDS, documentColors, elemStyle, fontLabel, styleKey, toHex } from './design';
import { columns, uid } from './model';
import { measuredHeight } from './Free';
import { ICONS, iconSvg } from './icons';

type Update = (fn: (d: CvData) => void) => void;

export type Sel = { type: 'el'; k: string; i?: string } | { type: 'free'; id: string } | null;

export interface Clip {
  el?: FreeEl;
  style?: ElemStyle;
}

// --- small controls ---------------------------------------------------------------------------------------------------

/** Keeps focus where it is (the text being edited) when a toolbar button is pressed. */
const keep = (e: React.MouseEvent) => e.preventDefault();

function Btn({ on, title, onClick, children, disabled, className = '' }: {
  on?: boolean; title: string; onClick: () => void; children: ReactNode; disabled?: boolean; className?: string;
}) {
  return (
    <button className={`tb-btn ${on ? 'on' : ''} ${className}`} title={title} onMouseDown={keep} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function Slider({ label, value, min, max, step, unit = '', onChange }: {
  label: string; value: number; min: number; max: number; step: number; unit?: string; onChange: (v: number) => void;
}) {
  return (
    <label className="slider">
      <span>
        {label}
        <input
          type="number"
          value={Number.isFinite(value) ? +value.toFixed(3) : ''}
          step={step}
          onChange={(e) => e.target.value !== '' && onChange(Number(e.target.value))}
        />
        {unit}
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function Pop({ id, open, setOpen, label, title, children, wide }: {
  id: string; open: string | null; setOpen: (id: string | null) => void; label: ReactNode; title: string; children: ReactNode; wide?: boolean;
}) {
  return (
    <span className="tb-pop-wrap">
      <button className={`tb-btn ${open === id ? 'on' : ''}`} title={title} onMouseDown={keep} onClick={() => setOpen(open === id ? null : id)}>
        {label}
      </button>
      {open === id && <div className={`tb-pop ${wide ? 'wide' : ''}`}>{children}</div>}
    </span>
  );
}

export function ColorPicker({ value, onChange, doc, none }: { value: string; onChange: (c: string | undefined) => void; doc: string[]; none?: string }) {
  const [hex, setHex] = useState(value);
  useEffect(() => setHex(value), [value]);
  const swatch = (c: string) => (
    <button key={c} className={`swatch ${c.toLowerCase() === value.toLowerCase() ? 'on' : ''}`} style={{ background: c }} title={c} onMouseDown={keep} onClick={() => onChange(c)} />
  );
  return (
    <div className="color-picker">
      <div className="cp-row">
        <label className="swatch custom" title="Pick any colour">
          <input type="color" value={/^#[0-9a-f]{6}$/i.test(value) ? value : '#000000'} onChange={(e) => onChange(e.target.value)} />+
        </label>
        <input
          className="hex"
          value={hex}
          onChange={(e) => {
            setHex(e.target.value);
            if (/^#[0-9a-f]{6}$/i.test(e.target.value)) onChange(e.target.value);
          }}
        />
        {none && (
          <button className="link" onMouseDown={keep} onClick={() => onChange(undefined)}>
            {none}
          </button>
        )}
      </div>
      {doc.length > 0 && (
        <>
          <h5>Document colours</h5>
          <div className="swatches">{doc.map(swatch)}</div>
        </>
      )}
      <h5>Default colours</h5>
      <div className="swatches">{DEFAULT_COLORS.map(swatch)}</div>
    </div>
  );
}

function FontPicker({ value, onChange, open, setOpen, id = 'font' }: { value: string; onChange: (f: string) => void; open: string | null; setOpen: (s: string | null) => void; id?: string }) {
  return (
    <Pop id={id} open={open} setOpen={setOpen} title="Font" label={<span className="font-label" style={{ fontFamily: value }}>{fontLabel(value) || 'Font'} ▾</span>}>
      <div className="font-list">
        {FONTS.map(([label, v]) => (
          <button
            key={v}
            className={v === value ? 'on' : ''}
            style={{ fontFamily: v }}
            onMouseDown={keep}
            onClick={() => {
              onChange(v);
              setOpen(null);
            }}
          >
            {label}
          </button>
        ))}
      </div>
    </Pop>
  );
}

function SizeStepper({ pt, onChange }: { pt: number; onChange: (pt: number) => void }) {
  const [text, setText] = useState(pt.toFixed(1));
  useEffect(() => setText(pt.toFixed(1)), [pt]);
  const step = (d: number) => onChange(Math.max(3, Math.round((pt + d) * 10) / 10));
  return (
    <span className="stepper" title="Text size (pt)">
      <button onMouseDown={keep} onClick={() => step(-0.5)}>−</button>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => Number(text) > 0 && onChange(Number(text))}
        onKeyDown={(e) => e.key === 'Enter' && Number(text) > 0 && onChange(Number(text))}
      />
      <button onMouseDown={keep} onClick={() => step(0.5)}>+</button>
    </span>
  );
}

const ALIGNS = ['left', 'center', 'right', 'justify'] as const;
const ALIGN_ICON: Record<string, string> = {
  left: 'M3 6h18M3 12h12M3 18h16',
  center: 'M3 6h18M6 12h12M4 18h16',
  right: 'M3 6h18M9 12h12M5 18h16',
  justify: 'M3 6h18M3 12h18M3 18h18',
};
const Svg = ({ d }: { d: string }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d={d} />
  </svg>
);

// --- what the selected thing looks like right now ------------------------------------------------------------------

interface Shown {
  font: string;
  pt: number;
  color: string;
  bg: string;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  upper: boolean;
  align: string;
  letterSpacing: number;
  lineHeight: number;
  opacity: number;
}

export function selectorFor(k: string, i?: string): string {
  if (k === 'page') return '.canvas .page';
  return `.canvas .page [data-k=${JSON.stringify(k)}]${i ? `[data-i=${JSON.stringify(i)}]` : ''}`;
}

function readShown(node: HTMLElement | null, basePt: number): Shown | null {
  const page = document.querySelector<HTMLElement>('.canvas .page');
  if (!node || !page) return null;
  const cs = getComputedStyle(node);
  const px = parseFloat(cs.fontSize);
  const pagePx = parseFloat(getComputedStyle(page).fontSize);
  const lh = parseFloat(cs.lineHeight);
  return {
    font: cs.fontFamily,
    pt: Math.round(((basePt * px) / pagePx) * 10) / 10,
    color: toHex(cs.color),
    bg: cs.backgroundColor === 'rgba(0, 0, 0, 0)' ? '' : toHex(cs.backgroundColor),
    bold: Number(cs.fontWeight) >= 600,
    italic: cs.fontStyle === 'italic',
    underline: cs.textDecorationLine.includes('underline'),
    upper: cs.textTransform === 'uppercase',
    align: cs.textAlign === 'start' ? 'left' : cs.textAlign,
    letterSpacing: cs.letterSpacing === 'normal' ? 0 : Math.round((parseFloat(cs.letterSpacing) / px) * 100) / 100,
    lineHeight: Number.isFinite(lh) ? Math.round((lh / px) * 100) / 100 : 1.3,
    opacity: Number(cs.opacity),
  };
}

// --- operations on free elements (also used by keyboard shortcuts and the right-click menu) ------------------------

export function freeOps(update: Update, setSel: (s: Sel) => void) {
  const at = (d: CvData, id: string) => d.design.free.findIndex((f) => f.id === id);
  return {
    duplicate(id: string) {
      const nid = uid('el');
      update((d) => {
        const i = at(d, id);
        if (i < 0) return;
        d.design.free.splice(i + 1, 0, { ...structuredClone(d.design.free[i]), id: nid, x: d.design.free[i].x + 4, y: d.design.free[i].y + 4, locked: false });
      });
      setSel({ type: 'free', id: nid });
    },
    paste(el: FreeEl) {
      const nid = uid('el');
      update((d) => void d.design.free.push({ ...structuredClone(el), id: nid, x: el.x + 4, y: el.y + 4 }));
      setSel({ type: 'free', id: nid });
    },
    remove(id: string) {
      update((d) => {
        d.design.free = d.design.free.filter((f) => f.id !== id);
        for (const v of d.variants) v.hidden = v.hidden.filter((h) => h !== id);
      });
      setSel(null);
    },
    /** Moves an element up or down among the elements of its own layer (behind or in front of the text). */
    layer(id: string, dir: 'forward' | 'backward' | 'front' | 'back') {
      update((d) => {
        const list = d.design.free;
        const i = at(d, id);
        if (i < 0) return;
        const el = list[i];
        const same = list.map((f, j) => (!!f.behind === !!el.behind ? j : -1)).filter((j) => j >= 0);
        const pos = same.indexOf(i);
        const target =
          dir === 'front' ? same[same.length - 1] : dir === 'back' ? same[0] : dir === 'forward' ? same[Math.min(pos + 1, same.length - 1)] : same[Math.max(pos - 1, 0)];
        list.splice(i, 1);
        list.splice(target, 0, el);
      });
    },
    set(id: string, fn: (f: FreeEl) => void) {
      update((d) => {
        const f = d.design.free.find((x) => x.id === id);
        if (f) fn(f);
      });
    },
    align(id: string, where: 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom') {
      update((d) => {
        const f = d.design.free.find((x) => x.id === id);
        if (!f) return;
        const h = measuredHeight(f);
        if (where === 'left') f.x = 0;
        if (where === 'center') f.x = Math.round((210 - f.w) * 50) / 100;
        if (where === 'right') f.x = Math.round((210 - f.w) * 100) / 100;
        if (where === 'top') f.y = 0;
        if (where === 'middle') f.y = Math.round((297 - h) * 50) / 100;
        if (where === 'bottom') f.y = Math.round((297 - h) * 100) / 100;
      });
    },
  };
}

/** Moves a section within its column or to the other column (per version). */
export function moveSection(v: Variant, key: SectionKey, how: 'up' | 'down' | 'other', layout: CvData['design']['layout']) {
  const cols = columns(v, layout.sidebar === 'none' ? { ...layout, sidebar: 'right' } : layout);
  const inMain = cols.main.includes(key);
  const list = inMain ? cols.main : cols.side;
  const i = list.indexOf(key);
  if (how === 'other') {
    list.splice(i, 1);
    (inMain ? cols.side : cols.main).push(key);
  } else {
    const j = how === 'up' ? i - 1 : i + 1;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
  }
  v.mainOrder = cols.main;
  v.sideOrder = cols.side;
}

// --- the toolbar --------------------------------------------------------------------------------------------------------

interface Props {
  data: CvData;
  variant: Variant;
  sel: Sel;
  setSel: (s: Sel) => void;
  update: Update;
  clip: React.MutableRefObject<Clip>;
  upload: (f: File) => Promise<string>;
  /** Change all elements of the selected kind, or only the selected one. */
  scope: 'all' | 'one';
  setScope: (s: 'all' | 'one') => void;
}

export function Toolbar({ data, variant, sel, setSel, update, clip, upload, scope, setScope }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [, force] = useState(0);
  const selKey = sel ? (sel.type === 'el' ? `${sel.k}#${sel.i ?? ''}` : sel.id) : '';
  useEffect(() => setOpen(null), [selKey]);
  // Read the selected element's look after each render of the page.
  useLayoutEffect(() => {
    const t = requestAnimationFrame(() => force((n) => n + 1));
    return () => cancelAnimationFrame(t);
  }, [data, selKey]);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      const t = e.target as HTMLElement;
      if (!t.closest('.tb-pop-wrap')) setOpen(null);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);

  const basePt = data.fontPt ?? data.style.fontSize;
  const doc = documentColors(data);
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const setV = (fn: (v: Variant) => void) => update((d) => fn(d.variants[vi]));
  const hidden = new Set(variant.hidden);
  const toggleHide = (id: string) => setV((v) => void (v.hidden = hidden.has(id) ? v.hidden.filter((h) => h !== id) : [...v.hidden, id]));
  const ops = freeOps(update, setSel);

  const pop = (id: string, title: string, label: ReactNode, children: ReactNode, wide?: boolean) => (
    <Pop id={id} open={open} setOpen={setOpen} title={title} label={label} wide={wide}>
      {children}
    </Pop>
  );

  /** Text controls; `s` is the stored style, `shown` what the element looks like now. */
  const textControls = (s: ElemStyle, shown: Shown, set: (p: Partial<ElemStyle>) => void) => (
    <>
      <FontPicker value={s.font ?? shown.font} onChange={(font) => set({ font })} open={open} setOpen={setOpen} />
      <SizeStepper pt={s.size !== undefined ? Math.round(s.size * basePt * 10) / 10 : shown.pt} onChange={(pt) => set({ size: Math.round((pt / basePt) * 1000) / 1000 })} />
      {pop(
        'color',
        'Text colour',
        <span className="color-a">
          A<i style={{ background: s.color ?? shown.color }} />
        </span>,
        <ColorPicker value={s.color ?? shown.color} doc={doc} onChange={(color) => set({ color })} none="Default" />,
      )}
      <Btn on={shown.bold} title="Bold" onClick={() => set({ bold: !shown.bold })}>
        <b>B</b>
      </Btn>
      <Btn on={shown.italic} title="Italic" onClick={() => set({ italic: !shown.italic })}>
        <i>I</i>
      </Btn>
      <Btn on={shown.underline} title="Underline" onClick={() => set({ underline: !shown.underline })}>
        <u>U</u>
      </Btn>
      <Btn on={shown.upper} title="Uppercase" onClick={() => set({ upper: !shown.upper })}>
        aA
      </Btn>
      <Btn title={`Alignment: ${shown.align}`} onClick={() => set({ align: ALIGNS[(ALIGNS.indexOf(shown.align as (typeof ALIGNS)[number]) + 1) % 4] })}>
        <Svg d={ALIGN_ICON[shown.align] ?? ALIGN_ICON.left} />
      </Btn>
      {pop(
        'spacing',
        'Spacing',
        'Spacing',
        <>
          <Slider label="Letter spacing" value={s.letterSpacing ?? shown.letterSpacing} min={-0.1} max={0.5} step={0.01} unit="em" onChange={(letterSpacing) => set({ letterSpacing })} />
          <Slider label="Line height" value={s.lineHeight ?? shown.lineHeight} min={0.8} max={2.5} step={0.05} onChange={(lineHeight) => set({ lineHeight })} />
          {sel?.type === 'el' && (
            <>
              <Slider label="Space above" value={s.spaceAbove ?? 0} min={-5} max={20} step={0.25} unit="mm" onChange={(spaceAbove) => set({ spaceAbove })} />
              <Slider label="Space below" value={s.spaceBelow ?? 0} min={-5} max={20} step={0.25} unit="mm" onChange={(spaceBelow) => set({ spaceBelow })} />
            </>
          )}
        </>,
      )}
      {pop(
        'effects',
        'Background, padding, corners and border',
        'Effects',
        <>
          <h5>Background</h5>
          <ColorPicker value={s.bg ?? (shown.bg || '#ffffff')} doc={doc} onChange={(bg) => set({ bg })} none="None" />
          <Slider label="Padding" value={s.padding ?? 0} min={0} max={10} step={0.25} unit="mm" onChange={(padding) => set({ padding })} />
          <Slider label="Corners" value={s.radius ?? 0} min={0} max={10} step={0.25} unit="mm" onChange={(radius) => set({ radius })} />
          <Slider label={sel?.type === 'el' && (sel.k === 'heading' || sel.k === 'sideHeading') ? 'Divider line' : 'Border'} value={s.borderWidth ?? 0} min={0} max={2} step={0.05} unit="mm" onChange={(borderWidth) => set({ borderWidth })} />
          <h5>{sel?.type === 'el' && (sel.k === 'heading' || sel.k === 'sideHeading') ? 'Divider colour' : 'Border colour'}</h5>
          <ColorPicker value={s.borderColor ?? '#d6d9de'} doc={doc} onChange={(borderColor) => set({ borderColor })} none="Default" />
        </>,
        true,
      )}
      {opacityPop(s.opacity ?? shown.opacity, (opacity) => set({ opacity }))}
    </>
  );

  const opacityPop = (value: number, set: (v: number) => void) =>
    pop('opacity', 'Transparency', <Svg d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 3v18M12 8h7M12 13h8M12 18h5" />, <Slider label="Transparency" value={Math.round((1 - value) * 100)} min={0} max={100} step={1} unit="%" onChange={(t) => set(Math.round(100 - t) / 100)} />);

  /** Box size: width and (minimum) height in mm, or automatic. */
  const sizePop = (s: ElemStyle, node: HTMLElement | null, set: (p: Partial<ElemStyle>) => void, heightToo = true) => {
    const mm = (px: number) => Math.round(px * (25.4 / 96) * 10) / 10;
    return pop(
      'size',
      'Box size (or drag the handles on the page)',
      <>
        <Svg d="M4 9V4h5M20 15v5h-5M4 4l7 7M20 20l-7-7" /> Size
      </>,
      <>
        <Slider label="Width" value={s.width ?? (node ? mm(node.offsetWidth) : 50)} min={3} max={210} step={0.5} unit="mm" onChange={(width) => set({ width })} />
        {heightToo && <Slider label="Height (at least)" value={s.height ?? (node ? mm(node.offsetHeight) : 5)} min={2} max={297} step={0.5} unit="mm" onChange={(height) => set({ height })} />}
        <div className="row-buttons">
          <button disabled={s.width === undefined} onClick={() => set({ width: undefined })}>Auto width</button>
          {heightToo && <button disabled={s.height === undefined} onClick={() => set({ height: undefined })}>Auto height</button>}
        </div>
        <p className="hint">Drag the handles on the right and bottom edge of the selected element to resize it on the page.</p>
      </>,
    );
  };

  let body: ReactNode;
  if (!sel) {
    body = (
      <span className="tb-hint">
        Click anything on the page to change how it looks. Add text, shapes, icons and images from the left.
        {pop('pagebg', 'Page background', 'Page colour', <ColorPicker value={data.design.layout.pageBg} doc={doc} onChange={(c) => update((d) => void (d.design.layout.pageBg = c ?? '#ffffff'))} none="White" />)}
      </span>
    );
  } else if (sel.type === 'el') {
    const kind = KINDS[sel.k] ?? { label: sel.k, cat: 'box' as const };
    const repeating = !!(kind.plural && sel.i);
    const key = repeating && scope === 'one' ? styleKey(sel.k, sel.i) : sel.k;
    const stored = elemStyle(data.design, sel.k, repeating ? sel.i : undefined);
    const own = sel.i ? data.design.elements[styleKey(sel.k, sel.i)] : undefined;
    const node = document.querySelector<HTMLElement>(selectorFor(sel.k, sel.i));
    const shown = readShown(node, basePt);
    const set = (p: Partial<ElemStyle>) =>
      update((d) => {
        const next: ElemStyle = { ...(d.design.elements[key] ?? {}), ...p };
        for (const [k, v] of Object.entries(p)) if (v === undefined) delete next[k as keyof ElemStyle];
        if (Object.keys(next).length) d.design.elements[key] = next;
        else delete d.design.elements[key];
      });
    const hideId = sel.k === 'section' ? sel.i : ['job', 'project', 'skill', 'bullet'].includes(sel.k) ? sel.i : undefined;
    const section = sel.k === 'section' ? (sel.i as SectionKey) : undefined;
    const pickImage = () => document.querySelector<HTMLInputElement>(`input[data-pick=${JSON.stringify(`${sel.k}${sel.i ? `#${sel.i}` : ''}`)}]`)?.click();

    body = (
      <>
        <span className="tb-kind">{kind.label}</span>
        {shown && kind.cat === 'text' && (
          <>
            {textControls(stored, shown, set)}
            {sizePop(stored, node, set)}
          </>
        )}
        {shown && kind.cat === 'box' && (
          <>
            {pop(
              'bg',
              'Background colour',
              <span className="color-a fill">
                <i style={{ background: stored.bg ?? (shown.bg || '#ffffff') }} />
              </span>,
              <ColorPicker
                value={stored.bg ?? (shown.bg || '#ffffff')}
                doc={doc}
                onChange={(bg) => (sel.k === 'sideCol' && bg ? update((d) => void (d.style.sidebarBg = bg)) : sel.k === 'page' ? update((d) => void (d.design.layout.pageBg = bg ?? '#ffffff')) : set({ bg }))}
                none="Default"
              />,
            )}
            <FontPicker value={stored.font ?? shown.font} onChange={(font) => set({ font })} open={open} setOpen={setOpen} />
            {pop(
              'color',
              'Text colour inside',
              <span className="color-a">
                A<i style={{ background: stored.color ?? shown.color }} />
              </span>,
              <ColorPicker value={stored.color ?? shown.color} doc={doc} onChange={(color) => set({ color })} none="Default" />,
            )}
            {sel.k !== 'page' &&
              pop(
                'box',
                'Spacing, corners and border',
                'Spacing',
                <>
                  <Slider label="Padding" value={stored.padding ?? 0} min={0} max={15} step={0.25} unit="mm" onChange={(padding) => set({ padding })} />
                  {sel.k !== 'mainCol' && sel.k !== 'sideCol' && (
                    <>
                      <Slider label="Space above" value={stored.spaceAbove ?? 0} min={-5} max={25} step={0.25} unit="mm" onChange={(spaceAbove) => set({ spaceAbove })} />
                      <Slider label="Space below" value={stored.spaceBelow ?? 0} min={-5} max={25} step={0.25} unit="mm" onChange={(spaceBelow) => set({ spaceBelow })} />
                    </>
                  )}
                  <Slider label="Corners" value={stored.radius ?? 0} min={0} max={10} step={0.25} unit="mm" onChange={(radius) => set({ radius })} />
                  <Slider label="Border" value={stored.borderWidth ?? 0} min={0} max={2} step={0.05} unit="mm" onChange={(borderWidth) => set({ borderWidth })} />
                  <h5>Border colour</h5>
                  <ColorPicker value={stored.borderColor ?? '#d6d9de'} doc={doc} onChange={(borderColor) => set({ borderColor })} />
                </>,
                true,
              )}
            {sel.k !== 'page' && sel.k !== 'mainCol' && sel.k !== 'sideCol' && sizePop(stored, node, set)}
            {opacityPop(stored.opacity ?? shown.opacity, (opacity) => set({ opacity }))}
          </>
        )}
        {kind.cat === 'image' && (
          <>
            {pop(
              'img',
              'Size and corners',
              'Size & shape',
              <>
                <Slider label="Size" value={stored.imgSize ?? (node ? Math.round(node.offsetWidth * (25.4 / 96) * 10) / 10 : 30)} min={3} max={80} step={0.5} unit="mm" onChange={(imgSize) => set({ imgSize })} />
                <Slider label="Roundness" value={stored.round ?? (sel.k === 'photo' ? 50 : 0)} min={0} max={50} step={1} unit="%" onChange={(round) => set({ round })} />
                <Slider label="Border" value={stored.borderWidth ?? 0} min={0} max={3} step={0.1} unit="mm" onChange={(borderWidth) => set({ borderWidth })} />
                <h5>Border colour</h5>
                <ColorPicker value={stored.borderColor ?? '#ffffff'} doc={doc} onChange={(borderColor) => set({ borderColor })} />
              </>,
              true,
            )}
            {opacityPop(stored.opacity ?? 1, (opacity) => set({ opacity }))}
            <Btn title="Replace the image" onClick={pickImage}>
              Replace
            </Btn>
          </>
        )}
        <span className="tb-sep" />
        {repeating && (
          <span className="seg" title="Change all of them or only this one">
            <button className={scope === 'all' ? 'on' : ''} onMouseDown={keep} onClick={() => setScope('all')}>
              All {kind.plural}
            </button>
            <button className={scope === 'one' ? 'on' : ''} onMouseDown={keep} onClick={() => setScope('one')}>
              Only this one
            </button>
          </span>
        )}
        {section && (
          <>
            <Btn title="Move the section up" onClick={() => setV((v) => moveSection(v, section, 'up', data.design.layout))}>↑</Btn>
            <Btn title="Move the section down" onClick={() => setV((v) => moveSection(v, section, 'down', data.design.layout))}>↓</Btn>
            {data.design.layout.sidebar !== 'none' && (
              <Btn title="Move the section to the other column" onClick={() => setV((v) => moveSection(v, section, 'other', data.design.layout))}>
                ⇄ Other column
              </Btn>
            )}
          </>
        )}
        {hideId && (
          <Btn title="Hide in this version (it stays in your profile)" onClick={() => toggleHide(hideId)}>
            Hide
          </Btn>
        )}
        {pop(
          'more',
          'Copy, paste or reset the style',
          '⋯',
          <div className="menu-list">
            <button onClick={() => { clip.current = { ...clip.current, style: stored }; setOpen(null); }}>Copy style <kbd>Ctrl+Alt+C</kbd></button>
            <button disabled={!clip.current.style} onClick={() => { set({ ...clip.current.style }); setOpen(null); }}>Paste style <kbd>Ctrl+Alt+V</kbd></button>
            <button
              disabled={!data.design.elements[key] && !(scope === 'all' && own)}
              onClick={() => {
                update((d) => {
                  delete d.design.elements[key];
                  if (scope === 'all' && sel.i) delete d.design.elements[styleKey(sel.k, sel.i)];
                });
                setOpen(null);
              }}
            >
              Reset to the template's look
            </button>
          </div>,
        )}
        {repeating && scope === 'all' && own && <span className="tb-note">This one has its own style</span>}
      </>
    );
  } else {
    const el = data.design.free.find((f) => f.id === sel.id);
    if (!el) body = null;
    else {
      const set = (fn: (f: FreeEl) => void) => ops.set(el.id, fn);
      const s = el.style ?? {};
      const node = document.querySelector<HTMLElement>(`.canvas [data-free="${el.id}"] .free-text`);
      const shown = el.type === 'text' ? readShown(node, basePt) : null;
      const fill = (label: string, value: string, apply: (c: string | undefined) => void, none?: string) =>
        pop(
          'fill',
          label,
          <span className="color-a fill">
            <i style={{ background: value }} />
          </span>,
          <ColorPicker value={value} doc={doc} onChange={apply} none={none} />,
        );
      const stroke = (withDash: boolean) => (
        <>
          <Slider label={el.type === 'line' ? 'Line weight' : 'Border'} value={el.strokeWidth ?? (el.type === 'line' ? 0.5 : 0)} min={0} max={el.type === 'icon' ? 4 : 5} step={0.05} unit={el.type === 'icon' ? '' : 'mm'} onChange={(v) => set((f) => void (f.strokeWidth = v))} />
          {withDash && (
            <span className="seg">
              {(['solid', 'dashed', 'dotted'] as const).map((d) => (
                <button key={d} className={(el.dash ?? 'solid') === d ? 'on' : ''} onClick={() => set((f) => void (f.dash = d))}>
                  {d}
                </button>
              ))}
            </span>
          )}
          {el.type !== 'line' && el.type !== 'icon' && (
            <>
              <h5>Border colour</h5>
              <ColorPicker value={el.stroke ?? '#000000'} doc={doc} onChange={(c) => set((f) => void (f.stroke = c))} />
            </>
          )}
        </>
      );
      body = (
        <>
          <span className="tb-kind">{{ text: 'Text box', rect: 'Shape', ellipse: 'Circle', line: 'Line', image: 'Image', icon: 'Icon' }[el.type]}</span>
          {el.type === 'text' && shown && textControls(s, shown, (p) => set((f) => {
            const next = { ...(f.style ?? {}), ...p };
            for (const [k, v] of Object.entries(p)) if (v === undefined) delete next[k as keyof ElemStyle];
            f.style = next;
          }))}
          {(el.type === 'rect' || el.type === 'ellipse') && (
            <>
              {fill('Fill colour', el.fill ?? 'transparent', (c) => set((f) => void (f.fill = c)), 'No fill')}
              {pop('stroke', 'Border', 'Border', <>{stroke(true)}{el.type === 'rect' && <Slider label="Corners" value={el.radius ?? 0} min={0} max={30} step={0.5} unit="mm" onChange={(v) => set((f) => void (f.radius = v))} />}</>, true)}
            </>
          )}
          {el.type === 'line' && (
            <>
              {fill('Line colour', el.stroke ?? '#000000', (c) => set((f) => void (f.stroke = c ?? '#000000')))}
              {pop('stroke', 'Line weight and style', 'Line style', stroke(true))}
            </>
          )}
          {el.type === 'icon' && (
            <>
              {fill('Icon colour', el.fill ?? '#000000', (c) => set((f) => void (f.fill = c ?? '#000000')))}
              {pop('stroke', 'Line weight', 'Weight', stroke(false))}
              {pop(
                'icon',
                'Change the icon',
                'Icon',
                <div className="icon-grid">
                  {Object.keys(ICONS).map((name) => (
                    <button key={name} title={name} className={el.icon === name ? 'on' : ''} onClick={() => set((f) => void (f.icon = name))} dangerouslySetInnerHTML={{ __html: iconSvg(name) }} />
                  ))}
                </div>,
                true,
              )}
            </>
          )}
          {el.type === 'image' && (
            <>
              {pop('stroke', 'Corners and border', 'Corners & border', <>
                <Slider label="Corners" value={el.radius ?? 0} min={0} max={Math.min(el.w, el.h) / 2} step={0.5} unit="mm" onChange={(v) => set((f) => void (f.radius = v))} />
                {stroke(false)}
              </>, true)}
              <label className="tb-btn file-btn" title="Replace the image">
                Replace
                <input
                  type="file"
                  accept="image/*"
                  hidden
                  onChange={async (e) => {
                    const f = e.target.files?.[0];
                    e.target.value = '';
                    if (f) {
                      const src = await upload(f);
                      set((x) => void (x.src = src));
                    }
                  }}
                />
              </label>
            </>
          )}
          {el.type !== 'text' && opacityPop(el.opacity ?? 1, (v) => set((f) => void (f.opacity = v)))}
          <span className="tb-sep" />
          {pop(
            'position',
            'Position, layers and alignment',
            'Position',
            <div className="position">
              <h5>Layer</h5>
              <div className="grid2">
                <button onClick={() => ops.layer(el.id, 'forward')}>Forward</button>
                <button onClick={() => ops.layer(el.id, 'backward')}>Backward</button>
                <button onClick={() => ops.layer(el.id, 'front')}>To front</button>
                <button onClick={() => ops.layer(el.id, 'back')}>To back</button>
              </div>
              <label className="check">
                <input type="checkbox" checked={!!el.behind} onChange={(e) => set((f) => void (f.behind = e.target.checked))} />
                Behind the CV text
              </label>
              <h5>Align to page</h5>
              <div className="grid3">
                {(['left', 'center', 'right', 'top', 'middle', 'bottom'] as const).map((w) => (
                  <button key={w} onClick={() => ops.align(el.id, w)}>
                    {w[0].toUpperCase() + w.slice(1)}
                  </button>
                ))}
              </div>
              <h5>Exact position (mm)</h5>
              <div className="grid-num">
                {(['x', 'y', 'w', 'h', 'rot'] as const)
                  .filter((k) => !(k === 'h' && (el.type === 'text' || el.type === 'line')))
                  .map((k) => (
                    <label key={k}>
                      <span>{{ x: 'X', y: 'Y', w: 'Width', h: 'Height', rot: 'Rotate°' }[k]}</span>
                      <input type="number" step={k === 'rot' ? 1 : 0.5} value={el[k]} onChange={(e) => e.target.value !== '' && set((f) => void (f[k] = Number(e.target.value)))} />
                    </label>
                  ))}
              </div>
            </div>,
            true,
          )}
          <Btn on={!!el.locked} title={el.locked ? 'Unlock' : 'Lock (stops moving it by accident)'} onClick={() => set((f) => void (f.locked = !f.locked))}>
            {el.locked ? 'Unlock' : 'Lock'}
          </Btn>
          <Btn title="Hide in this version only" onClick={() => toggleHide(el.id)}>
            {hidden.has(el.id) ? 'Show' : 'Hide'}
          </Btn>
          <Btn title="Duplicate (Ctrl+D)" onClick={() => ops.duplicate(el.id)}>
            Duplicate
          </Btn>
          <Btn title="Delete (Del)" className="danger" onClick={() => ops.remove(el.id)}>
            Delete
          </Btn>
        </>
      );
    }
  }
  return <div className="toolbar">{body}</div>;
}
