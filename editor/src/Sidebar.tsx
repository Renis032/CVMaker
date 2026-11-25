// The left side: an icon rail and the drawer it opens, like Canva's side panel.
import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { CvData, FreeEl, PageLayout, Style, Variant } from './types';
import { FONTS, FONT_SETS, PALETTES, TEMPLATES, applyFontSet, applyTemplate, documentColors, fontLabel, templateFrom, type Template } from './design';
import { ICONS, iconSvg } from './icons';
import { API } from './model';
import { ContentPanel } from './Content';
import { photoBack } from './items';
import { Dropdown } from './ui';
import { newFree } from './Free';
import { ColorPicker, Slider, type Sel } from './Toolbar';
import { VersionsPanel } from './Panels';
import { ChatPanel } from './Chat';

type Update = (fn: (d: CvData) => void) => void;

export type Tab = 'content' | 'insert' | 'chat' | 'design' | 'layout' | 'versions' | 'layers' | 'job' | 'history';

/** Older saved tabs (before Elements, Text and Uploads became Insert). */
export function normalTab(t: string | null): Tab | null {
  if (t === 'elements' || t === 'text' || t === 'uploads') return 'insert';
  return (['content', 'insert', 'chat', 'design', 'layout', 'versions', 'layers', 'job', 'history'] as string[]).includes(t ?? '') ? (t as Tab) : null;
}

/** The rail, in groups: what is on the CV, how it looks, and managing versions and changes. */
const GROUPS: [string, Tab[]][] = [
  ['Add', ['content', 'insert', 'chat']],
  ['Look', ['design', 'layout']],
  ['Manage', ['versions', 'layers', 'job', 'history']],
];

const TABS: [Tab, string, string][] = [
  ['content', 'Content', '<path d="M4 6h16M4 12h10M4 18h13"/><circle cx="19" cy="17" r="3"/><path d="M19 15.5v3M17.5 17h3"/>'],
  ['insert', 'Insert', '<circle cx="7" cy="7" r="4"/><rect x="13" y="3" width="8" height="8" rx="1"/><path d="M7 13l5 8H2z"/><path d="M14 17h7M17.5 13.5v7"/>'],
  ['chat', 'Chat', '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 11h7M8.5 14h4"/>'],
  ['design', 'Design', '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'],
  ['layout', 'Layout', '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18M3 9h12"/>'],
  ['versions', 'Versions', '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>'],
  ['layers', 'Layers', '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 12 12 17 22 12"/>'],
  ['job', 'Job match', '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>'],
  ['history', 'History', '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>'],
];

interface Props {
  data: CvData;
  variant: Variant;
  update: Update;
  select: (id: string) => void;
  sel: Sel;
  setSel: (s: Sel) => void;
  upload: (f: File) => Promise<string>;
  tab: Tab | null;
  setTab: (t: Tab | null) => void;
  itemAction: (what: 'remove' | 'copy' | 'new' | 'up' | 'down' | 'hide', k: string, i?: string) => void;
}

export function Sidebar(props: Props) {
  const { tab, setTab } = props;
  return (
    <>
      <nav className="rail">
        {GROUPS.map(([group, ids]) => (
          <div key={group} className="rail-group">
            <span className="rail-cap">{group}</span>
            {TABS.filter(([id]) => ids.includes(id) && (id !== 'job' || props.data.job)).map(([id, label, icon]) => (
              <button key={id} className={tab === id ? 'on' : ''} onClick={() => setTab(tab === id ? null : id)} title={TAB_HINTS[id]}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: icon }} />
                <span>{label}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>
      {tab && (
        <aside className="drawer" key={tab}>
          <button className="drawer-close" title="Close" onClick={() => setTab(null)}>
            ‹
          </button>
          {tab === 'content' && <ContentPanel data={props.data} variant={props.variant} update={props.update} setSel={props.setSel} upload={props.upload} itemAction={props.itemAction} />}
          {tab === 'insert' && (
            <>
              <nav className="insert-nav">
                {[['ins-parts', 'CV parts'], ['ins-text', 'Text'], ['ins-shapes', 'Shapes'], ['ins-icons', 'Icons'], ['ins-images', 'Images']].map(([id, l]) => (
                  <button key={id} onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })}>
                    {l}
                  </button>
                ))}
              </nav>
              <div id="ins-parts"><PartsTab {...props} /></div>
              <p className="hint insert-hint">Below: free objects you place anywhere on the page, on top of (or behind) your CV's text.</p>
              <div id="ins-text"><TextTab {...props} /></div>
              <ElementsTab {...props} />
              <div id="ins-images"><UploadsTab {...props} /></div>
            </>
          )}
          {tab === 'chat' && <ChatPanel data={props.data} variant={props.variant} update={props.update} />}
          {tab === 'design' && <DesignTab {...props} />}
          {tab === 'layout' && <LayoutTab {...props} />}
          {tab === 'versions' && <VersionsPanel data={props.data} variant={props.variant} update={props.update} select={props.select} />}
          {tab === 'layers' && <LayersTab {...props} />}
          {tab === 'job' && props.data.job && <JobTab {...props} />}
          {tab === 'history' && <HistoryTab {...props} />}
        </aside>
      )}
    </>
  );
}

const TAB_HINTS: Record<Tab, string> = {
  content: 'Content: sections and their items, the photo, new sections',
  insert: 'Insert: text boxes, shapes, lines, icons and images placed anywhere',
  chat: 'Chat: ask Claude for suggestions or changes to this CV',
  design: 'Design: templates, your templates, colour palettes, font sets',
  layout: 'Layout: page arrangement, text size and spacing, colours',
  versions: 'Versions: your CV versions, their names and own wording',
  layers: 'Layers: order of the objects on the page',
  job: 'Job match: what the job asks for and whether this CV shows it',
  history: 'History: restore an earlier state of your CV',
};

function add(props: Props, el: FreeEl) {
  props.update((d) => void d.design.free.push(el));
  props.setSel({ type: 'free', id: el.id });
}

// --- Design: templates, colour palettes, font sets ----------------------------------------------------------------------

function TemplateCard({ t, onClick, onDelete }: { t: Template; onClick: () => void; onDelete?: () => void }) {
  const c = t.colors;
  const L = t.layout;
  const side = L.sidebar ?? 'right';
  const headerBg = t.elements?.header?.bg;
  return (
    <button className="tpl" onClick={onClick} title={`Use the "${t.name}" design`}>
      {onDelete && (
        <span className="tpl-del" role="button" title="Delete this template" onClick={(e) => (e.stopPropagation(), onDelete())}>
          ×
        </span>
      )}
      <div className="tpl-page" style={{ flexDirection: side === 'left' ? 'row-reverse' : 'row', fontFamily: t.font }}>
        {L.header === 'full' && <div className="tpl-banner" style={{ background: headerBg }} />}
        <div className="tpl-main">
          {L.header !== 'full' && <div className="tpl-name" style={{ background: c.nameColor }} />}
          {[0, 1, 2].map((i) => (
            <div key={i}>
              <div className="tpl-h" style={{ background: c.heading }} />
              <div className="tpl-line" />
              <div className="tpl-line short" />
            </div>
          ))}
        </div>
        {side !== 'none' && (
          <div className="tpl-side" style={{ background: c.sidebarBg }}>
            {L.photo !== 'header' && L.photo !== 'none' && <div className="tpl-photo" style={{ borderColor: c.sidebarLabel }} />}
            <div className="tpl-h" style={{ background: c.sidebarLabel }} />
            <div className="tpl-line" style={{ background: c.sidebarText, opacity: 0.4 }} />
            <div className="tpl-h" style={{ background: c.sidebarLabel }} />
            <div className="tpl-line" style={{ background: c.sidebarText, opacity: 0.4 }} />
          </div>
        )}
      </div>
      <span>{t.name}</span>
    </button>
  );
}

function DesignTab({ data, update }: Props) {
  const saved = data.design.saved ?? [];
  const saveCurrent = () => {
    const name = prompt('Name for this template:', `My template ${saved.length + 1}`)?.trim();
    if (!name) return;
    update((d) => {
      const list = (d.design.saved ?? []).filter((t) => t.name !== name);
      d.design.saved = [...list, templateFrom(d, name)];
    });
  };
  return (
    <div className="panel">
      <h3>My templates</h3>
      <p className="hint">
        Start from any template below, change colours, fonts, spacing and element styles until it looks right, then save it here to
        reuse it (also on a new version or a tailored CV).
      </p>
      {saved.length > 0 && (
        <div className="tpl-grid">
          {saved.map((t) => (
            <TemplateCard
              key={t.name}
              t={t}
              onClick={() => update((d) => applyTemplate(d, t))}
              onDelete={() => confirm(`Delete the template “${t.name}”?`) && update((d) => void (d.design.saved = (d.design.saved ?? []).filter((x) => x.name !== t.name)))}
            />
          ))}
        </div>
      )}
      <button className="primary save-tpl" onClick={saveCurrent}>
        Save the current design as a template
      </button>

      <h3>Templates</h3>
      <p className="hint">Changes the layout, colours and fonts. Your text stays as it is. Ctrl+Z undoes it.</p>
      <div className="tpl-grid">
        {TEMPLATES.map((t) => (
          <TemplateCard key={t.name} t={t} onClick={() => update((d) => applyTemplate(d, t))} />
        ))}
      </div>
      <h3>Colour palettes</h3>
      <div className="palettes">
        {PALETTES.map(([name, p]) => (
          <button key={name} className="palette" title={name} onClick={() => update((d) => void Object.assign(d.style, p))}>
            {[p.nameColor, p.accent, p.sidebarBg, p.sidebarLabel, p.text].map((c, i) => (
              <i key={i} style={{ background: c }} />
            ))}
            <span>{name}</span>
          </button>
        ))}
      </div>
      <h3>Font sets</h3>
      <div className="font-sets">
        {FONT_SETS.map(([head, body]) => (
          <button key={head + body} className={`font-set ${data.style.font === body ? 'on' : ''}`} onClick={() => update((d) => applyFontSet(d, head, body))}>
            <strong style={{ fontFamily: head }}>{fontLabel(head)}</strong>
            <span style={{ fontFamily: body }}>{fontLabel(body)} for the text</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Elements: shapes, lines, icons ------------------------------------------------------------------------------------

function ElementsTab(props: Props) {
  const accent = props.data.style.accent;
  const shapes: [string, () => FreeEl, ReactNode][] = [
    ['Rectangle', () => newFree('rect', { fill: accent }), <div className="shape-prev" style={{ background: accent }} />],
    ['Rounded', () => newFree('rect', { fill: accent, radius: 4 }), <div className="shape-prev" style={{ background: accent, borderRadius: 8 }} />],
    ['Circle', () => newFree('ellipse', { fill: accent }), <div className="shape-prev" style={{ background: accent, borderRadius: '50%', width: 34 }} />],
    ['Outline', () => newFree('rect', { strokeWidth: 0.4, stroke: accent }), <div className="shape-prev" style={{ border: `2px solid ${accent}` }} />],
    ['Line', () => newFree('line', { stroke: accent, strokeWidth: 0.5 }), <div className="line-prev" style={{ borderTop: `3px solid ${accent}` }} />],
    ['Dashed line', () => newFree('line', { stroke: accent, strokeWidth: 0.4, dash: 'dashed' }), <div className="line-prev" style={{ borderTop: `3px dashed ${accent}` }} />],
    ['Dotted line', () => newFree('line', { stroke: accent, strokeWidth: 0.6, dash: 'dotted' }), <div className="line-prev" style={{ borderTop: `4px dotted ${accent}` }} />],
    ['Accent bar', () => newFree('rect', { fill: accent, w: 30, h: 1.5 }), <div className="shape-prev" style={{ background: accent, height: 6 }} />],
    ['Full-width band', () => newFree('rect', { fill: accent, w: 210, h: 40, x: 0, y: 0, behind: true }), <div className="shape-prev" style={{ background: accent, width: 56, height: 14 }} />],
  ];
  return (
    <div className="panel">
      <h3 id="ins-shapes">Shapes and lines</h3>
      <div className="el-grid">
        {shapes.map(([label, make, prev]) => (
          <button key={label} className="el-tile" title={`Add: ${label}`} onClick={() => add(props, make())}>
            {prev}
            <span>{label}</span>
          </button>
        ))}
      </div>
      <h3 id="ins-icons">Icons</h3>
      <div className="icon-grid big">
        {Object.keys(ICONS).map((name) => (
          <button key={name} title={name} style={{ color: accent }} onClick={() => add(props, newFree('icon', { icon: name, fill: accent }))} dangerouslySetInnerHTML={{ __html: iconSvg(name) }} />
        ))}
      </div>
      <p className="hint">Drag to move, corners to resize, ⟳ to rotate. Arrow keys nudge (Shift for more). Hold Alt while dragging to stop snapping.</p>
    </div>
  );
}

// --- CV parts: the photo and anything taken off the page -----------------------------------------------------------------

const PART_ICON = {
  photo: '<circle cx="12" cy="9" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  section: '<path d="M4 6h16M4 11h10M4 16h13"/>',
  sidebar: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
  logo: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M9 12h6M12 9v6"/>',
};

/** Parts of the CV itself (not free objects): put back the photo, a section, the sidebar or logo spots you deleted. */
function PartsTab({ data, variant, update, setSel }: Props) {
  const L = data.design.layout;
  const c = data.content;
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const photoOn = L.photo !== 'none';
  const removed = (['summary', 'experience', 'projects', 'contact', 'skills', 'softSkills', 'languages', 'interests', 'education'] as const).filter((k) => variant.hidden.includes(k));
  const noLogo = c.experience.filter((j) => j.logo === 'none');
  const select = (k: string, i?: string) => requestAnimationFrame(() => {
    setSel({ type: 'el', k, i });
    document.querySelector(`.canvas .page [data-k="${k}"]${i ? `[data-i="${i}"]` : ''}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  });
  const tile = (key: string, label: string, icon: string, run: () => void, title: string, done = false) => (
    <button key={key} className={`el-tile part ${done ? 'done' : ''}`} title={title} onClick={run}>
      <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: icon }} />
      <span>{label}</span>
    </button>
  );
  return (
    <div className="panel">
      <h3>CV parts</h3>
      <p className="hint">Parts of your CV itself. Click one to put it on the page (or select it there), then fill it in. Anything you delete shows up here to bring back.</p>
      <div className="el-grid">
        {tile(
          'photo',
          photoOn ? (c.photo ? 'Profile photo (on the page)' : 'Profile photo (spot on the page)') : 'Profile photo',
          PART_ICON.photo,
          () => {
            if (!photoOn) update((d) => photoBack(d));
            select('photo');
          },
          photoOn ? 'Select the photo on the page (then “+ Add photo” or “Replace” in the toolbar)' : 'Put the photo spot back on the page, then choose an image with “+ Add photo” in the toolbar',
          photoOn,
        )}
        {L.sidebar === 'none' &&
          tile('sidebar', 'Sidebar', PART_ICON.sidebar, () => update((d) => void (d.design.layout.sidebar = 'right')), 'Bring back the sidebar (Layout → Sidebar chooses left or right)')}
        {removed.map((k) =>
          tile(k, c.headings[k] || k, PART_ICON.section, () => {
            update((d) => void (d.variants[vi].hidden = d.variants[vi].hidden.filter((x) => x !== k)));
            select('section', k);
          }, 'Put this section back on the page in this version'),
        )}
        {noLogo.length > 0 &&
          tile('logos', `Logo spots (${noLogo.length} job${noLogo.length > 1 ? 's' : ''})`, PART_ICON.logo, () => {
            update((d) => d.content.experience.forEach((j) => void (j.logo === 'none' && (j.logo = ''))));
            select('jobLogo', noLogo[0].id);
          }, 'Bring back the company logo spots you deleted')}
      </div>
      <p className="hint">Sections and their items (jobs, skill groups, your own sections…) are added in <strong>Content</strong>, or with the “+” buttons on the page.</p>
    </div>
  );
}

// --- Text ---------------------------------------------------------------------------------------------------------------

function TextTab(props: Props) {
  const c = props.data.style;
  const kinds: [string, string, Partial<FreeEl>][] = [
    ['Add a heading', 'heading', { text: 'Heading', style: { size: 2.4, bold: true, color: c.nameColor } }],
    ['Add a subheading', 'subheading', { text: 'Subheading', style: { size: 1.5, bold: true, color: c.heading } }],
    ['Add a little body text', 'body', { text: 'Body text', style: { size: 1, color: c.text } }],
  ];
  return (
    <div className="panel">
      <h3>Text</h3>
      {kinds.map(([label, cls, extra]) => (
        <button key={cls} className={`add-text ${cls}`} onClick={() => add(props, newFree('text', { ...extra, w: cls === 'body' ? 60 : 90 }))}>
          {label}
        </button>
      ))}
      <p className="hint">
        Text boxes go anywhere on the page. Double-click one to type; <kbd>Enter</kbd> starts a new line, <kbd>Ctrl</kbd>+<kbd>B</kbd> bolds.
        To change your CV's own text, click it on the page.
      </p>
    </div>
  );
}

// --- Uploads -------------------------------------------------------------------------------------------------------------

function UploadsTab(props: Props) {
  const [files, setFiles] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const load = () =>
    fetch(`${API}uploads`)
      .then((r) => r.json())
      .then((b) => setFiles(b.files ?? []))
      .catch(() => {});
  useEffect(() => void load(), []);

  const place = (src: string) => {
    const img = new Image();
    img.onload = () => {
      const w = 45;
      const h = Math.round((w * img.naturalHeight) / Math.max(1, img.naturalWidth) * 10) / 10;
      add(props, newFree('image', { src, w, h }));
    };
    img.src = src;
  };
  return (
    <div className="panel">
      <h3>Uploads</h3>
      <label className="upload-btn">
        {busy ? 'Uploading…' : 'Upload images'}
        <input
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={async (e) => {
            const list = [...(e.target.files ?? [])];
            e.target.value = '';
            setBusy(true);
            try {
              let last = '';
              for (const f of list) last = await props.upload(f);
              if (last) setPicked(last); // uploaded to your images; you choose what to do with it
            } finally {
              setBusy(false);
              load();
            }
          }}
        />
      </label>
      <p className="hint">Click an image to select it, then choose what to do with it.</p>
      {picked && (
        <div className="upload-actions">
          <button className="primary" onClick={() => place(picked)}>Add to page</button>
          <button onClick={() => props.update((d) => {
            d.content.photo = picked;
            if (d.design.layout.photo === 'none') photoBack(d);
          })}>Use as photo</button>
          <button
            className="danger"
            onClick={async () => {
              if (!confirm('Delete this image from your images?')) return;
              const res = await fetch(`${API}uploads?path=${encodeURIComponent(picked)}`, { method: 'DELETE' });
              const body = await res.json().catch(() => ({}));
              if (!res.ok) return setErr(body.error || 'Could not delete it');
              setErr('');
              setPicked(null);
              load();
            }}
          >
            Delete
          </button>
          <button className="ghost" onClick={() => setPicked(null)}>Cancel</button>
        </div>
      )}
      {err && <p className="hint err">{err}</p>}
      <div className="uploads">
        {files.map((src) => (
          <button key={src} className={`upload ${picked === src ? 'on' : ''}`} onClick={() => setPicked(picked === src ? null : src)} title="Select">
            <img src={src} alt="" />
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Layout: page arrangement, text and spacing, base colours -------------------------------------------------------

type NumKey = { [K in keyof Style]: Style[K] extends number ? K : never }[keyof Style];
type ColorKey = { [K in keyof Style]: Style[K] extends string ? K : never }[keyof Style];

const NUMBERS: [NumKey, string, number, number, number, string][] = [
  ['fontSize', 'Text size', 6, 12, 0.1, 'pt'],
  ['lineHeight', 'Line height', 0.9, 1.8, 0.05, ''],
  ['sectionGap', 'Space between sections', 0, 12, 0.5, 'mm'],
  ['itemGap', 'Space between items', 0, 10, 0.5, 'mm'],
  ['sidebarWidth', 'Sidebar width', 20, 50, 0.5, '%'],
  ['photoSize', 'Photo size', 0, 60, 1, 'mm'],
];

const COLORS: [ColorKey, string][] = [
  ['nameColor', 'Name'],
  ['accent', 'Accent (title, company)'],
  ['heading', 'Section headings'],
  ['text', 'Body text'],
  ['muted', 'Dates and tech'],
  ['sidebarBg', 'Sidebar background'],
  ['sidebarText', 'Sidebar text'],
  ['sidebarLabel', 'Sidebar labels'],
];

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="choice">
      <span>{label}</span>
      <div className="seg">
        {options.map(([v, l]) => (
          <button key={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}

function LayoutTab({ data, update }: Props) {
  const tailored = data.mode === 'cv';
  const style = data.fontPt ? { ...data.style, fontSize: data.fontPt } : data.style;
  const L = data.design.layout;
  const [open, setOpen] = useState<string | null>(null);
  const doc = documentColors(data);
  const setL = <K extends keyof PageLayout>(k: K, v: PageLayout[K]) => update((d) => void (d.design.layout[k] = v));
  const set = <K extends keyof Style>(k: K, v: Style[K]) =>
    update((d) => {
      // A tailored CV keeps its own text size (it is fitted to one page); everything else is shared.
      if (tailored && k === 'fontSize') d.fontPt = v as number;
      else d.style[k] = v;
    });
  const customised = Object.keys(data.design.elements).length;
  return (
    <div className="panel">
      <h3>Page layout</h3>
      <Choice label="Sidebar" value={L.sidebar} options={[['left', 'Left'], ['right', 'Right'], ['none', 'None']]} onChange={(v) => setL('sidebar', v)} />
      <Choice label="Name and title" value={L.header} options={[['main', 'Main column'], ['full', 'Full width']]} onChange={(v) => setL('header', v)} />
      <Choice label="Photo" value={L.photo} options={[['side', 'Sidebar'], ['header', 'By the name'], ['none', 'None']]} onChange={(v) => setL('photo', v)} />
      <Choice label="Section headings" value={L.headingStyle} options={[['underline', 'Underlined'], ['plain', 'Plain'], ['bar', 'Filled'], ['side', 'Side bar']]} onChange={(v) => setL('headingStyle', v)} />
      <Choice label="Bullets" value={L.bullet} options={[['default', 'Default'], ['•', '•'], ['–', '–'], ['▸', '▸'], ['✓', '✓'], ['○', '○'], ['none', 'None']]} onChange={(v) => setL('bullet', v)} />
      <label className="check">
        <input type="checkbox" checked={L.contactIcons} onChange={(e) => setL('contactIcons', e.target.checked)} />
        Icons next to contact lines
      </label>
      <div className="color-row">
        <button className="color-btn" onClick={() => setOpen(open === 'pageBg' ? null : 'pageBg')}>
          <i style={{ background: L.pageBg }} /> Page background
        </button>
        {open === 'pageBg' && <ColorPicker value={L.pageBg} doc={doc} onChange={(c) => setL('pageBg', c ?? '#ffffff')} none="White" />}
      </div>

      <h3>Text and spacing</h3>
      <div className="field">
        <span>Font</span>
        <Dropdown value={style.font} onChange={(v) => set('font', v)} options={FONTS.map(([label, value]) => ({ value, label, style: { fontFamily: value } }))} />
      </div>
      {NUMBERS.map(([k, label, min, max, step, unit]) => (
        <Slider key={k} label={label} value={style[k]} min={min} max={max} step={step} unit={unit} onChange={(v) => set(k, v)} />
      ))}
      {tailored && <p className="hint">Text size is this CV's own (it is fitted to one page); everything else is shared by all your CVs.</p>}

      <h3>Colours</h3>
      {COLORS.map(([k, label]) => (
        <div key={k} className="color-row">
          <button className="color-btn" onClick={() => setOpen(open === k ? null : k)}>
            <i style={{ background: style[k] }} /> {label}
          </button>
          {open === k && <ColorPicker value={style[k]} doc={doc} onChange={(c) => c && set(k, c)} />}
        </div>
      ))}

      <h3>Element styles</h3>
      <p className="hint">
        {customised
          ? `${customised} element style${customised > 1 ? 's' : ''} changed from the template (select an element on the page to see or reset it).`
          : 'Nothing changed yet: click any element on the page to change its font, size, colour, spacing…'}
      </p>
      {customised > 0 && (
        <button className="danger" onClick={() => confirm('Reset every element to the template’s look? (Ctrl+Z undoes it.)') && update((d) => void (d.design.elements = {}))}>
          Reset all element styles
        </button>
      )}
      <p className="hint">The design is shared by all your versions and by the CVs CVMaker tailors for jobs.</p>
    </div>
  );
}

// --- Layers -------------------------------------------------------------------------------------------------------------

const TEXT_ROW = '__cv_text__';

function LayerRow({ id, label, preview, selected, hidden, locked, onClick, onHide, onLock }: {
  id: string; label: string; preview?: ReactNode; selected?: boolean; hidden?: boolean; locked?: boolean;
  onClick?: () => void; onHide?: () => void; onLock?: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <li
      ref={setNodeRef}
      className={`layer ${selected ? 'on' : ''} ${isDragging ? 'dragging' : ''} ${id === TEXT_ROW ? 'text-row' : ''} ${hidden ? 'is-hidden' : ''}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      onClick={onClick}
    >
      <span className="grip" {...attributes} {...listeners} title="Drag to change the order">
        ⠿
      </span>
      <span className="layer-prev">{preview}</span>
      <span className="sort-label">{label}</span>
      {onLock && (
        <button className="eye" onClick={(e) => (e.stopPropagation(), onLock())} title={locked ? 'Unlock' : 'Lock'}>
          {locked ? 'Locked' : 'Lock'}
        </button>
      )}
      {onHide && (
        <button className="eye" onClick={(e) => (e.stopPropagation(), onHide())} title="Show or hide in this version">
          {hidden ? 'Hidden' : 'Shown'}
        </button>
      )}
    </li>
  );
}

function freeLabel(f: FreeEl): string {
  if (f.type === 'text') return (f.text || 'Text').replace(/\*\*/g, '').split('\n')[0].slice(0, 40);
  return { rect: 'Shape', ellipse: 'Circle', line: 'Line', image: 'Image', icon: `Icon: ${f.icon}`, text: '' }[f.type];
}

function freePreview(f: FreeEl): ReactNode {
  if (f.type === 'image') return <img src={f.src} alt="" />;
  if (f.type === 'icon') return <i style={{ color: f.fill }} dangerouslySetInnerHTML={{ __html: iconSvg(f.icon ?? 'star') }} />;
  if (f.type === 'text') return <b>T</b>;
  if (f.type === 'line') return <i style={{ borderTop: `2px ${f.dash ?? 'solid'} ${f.stroke}`, width: 18, height: 0, marginTop: 9 }} />;
  return <i style={{ background: f.fill || 'transparent', border: f.strokeWidth ? `1px solid ${f.stroke}` : undefined, borderRadius: f.type === 'ellipse' ? '50%' : 2, width: 18, height: 14 }} />;
}

function LayersTab({ data, update, variant, sel, setSel }: Props) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 3 } }));
  const free = data.design.free;
  // Top of the list = front of the page.
  const front = free.filter((f) => !f.behind).reverse();
  const behind = free.filter((f) => f.behind).reverse();
  const ids = [...front.map((f) => f.id), TEXT_ROW, ...behind.map((f) => f.id)];
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const hidden = new Set(variant.hidden);

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return;
    const next = arrayMove(ids, ids.indexOf(String(e.active.id)), ids.indexOf(String(e.over.id)));
    const cut = next.indexOf(TEXT_ROW);
    update((d) => {
      const byId = new Map(d.design.free.map((f) => [f.id, f]));
      const fr = next.slice(0, cut).reverse().map((id) => ({ ...byId.get(id)!, behind: false }));
      const bh = next.slice(cut + 1).reverse().map((id) => ({ ...byId.get(id)!, behind: true }));
      d.design.free = [...bh, ...fr];
    });
  };

  return (
    <div className="panel">
      <h3>Layers</h3>
      <p className="hint">Top of the list is in front. Drag an element below “Your CV text” to put it behind the text.</p>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          <ul className="sort-list">
            {ids.map((id) => {
              if (id === TEXT_ROW) return <LayerRow key={id} id={id} label="Your CV text" preview={<b>¶</b>} />;
              const f = free.find((x) => x.id === id)!;
              return (
                <LayerRow
                  key={id}
                  id={id}
                  label={freeLabel(f)}
                  preview={freePreview(f)}
                  selected={sel?.type === 'free' && sel.id === id}
                  hidden={hidden.has(id)}
                  locked={f.locked}
                  onClick={() => setSel({ type: 'free', id })}
                  onLock={() => update((d) => void (d.design.free.find((x) => x.id === id)!.locked = !f.locked))}
                  onHide={() =>
                    update((d) => {
                      const v = d.variants[vi];
                      v.hidden = hidden.has(id) ? v.hidden.filter((h) => h !== id) : [...v.hidden, id];
                    })
                  }
                />
              );
            })}
          </ul>
        </SortableContext>
      </DndContext>
      {!free.length && <p className="hint">No elements yet. Add text, shapes, icons or images from the tabs above.</p>}
    </div>
  );
}


// --- Job match: which of the posting's key skills this CV shows --------------------------------------------------

const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** The term as a whole word (so "java" doesn't match "javascript", and "c++" works). */
export function mentions(text: string, term: string): boolean {
  return new RegExp(`(?<![a-z0-9])${esc(term.toLowerCase())}(?![a-z0-9])`).test(text.toLowerCase());
}

function JobTab({ data, variant, update, setSel }: Props) {
  const job = data.job!;
  const [pageText, setPageText] = useState('');
  // Read what the page actually shows (hidden parts and editing buttons are not part of it).
  useLayoutEffect(() => {
    const t = document.querySelector<HTMLElement>('.canvas .page')?.innerText ?? '';
    if (t !== pageText) setPageText(t);
  });
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const hidden = new Set(variant.hidden);
  const c = data.content;
  /** A part of the CV that mentions the term but is hidden in this CV. */
  const hiddenHit = (term: string): { id: string; what: string } | null => {
    for (const j of c.experience) {
      if (hidden.has(j.id) && [j.role, ...j.bullets.map((b) => b.text)].some((t) => mentions(t, term))) return { id: j.id, what: `job “${j.role}”` };
      for (const b of j.bullets) if (hidden.has(b.id) && mentions(b.text, term)) return { id: b.id, what: `bullet in ${j.company}` };
    }
    for (const p of c.projects) {
      if (hidden.has(p.id) && [p.title, p.tech, ...p.bullets.map((b) => b.text)].some((t) => mentions(t, term))) return { id: p.id, what: `project “${p.title}”` };
      for (const b of p.bullets) if (hidden.has(b.id) && mentions(b.text, term)) return { id: b.id, what: `bullet in ${p.title}` };
    }
    for (const g of c.skills) if (hidden.has(g.id) && mentions(`${g.label} ${g.value}`, term)) return { id: g.id, what: `skill group “${g.label}”` };
    return null;
  };
  const rows = job.keywords.map((k) => ({ k, ok: mentions(pageText, k) }));
  const covered = rows.filter((r) => r.ok).length;
  const pct = rows.length ? Math.round((covered / rows.length) * 100) : 0;
  const goToSkills = () => {
    setSel({ type: 'el', k: 'section', i: 'skills' });
    document.querySelector('.canvas [data-k="section"][data-i="skills"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  return (
    <div className="panel">
      <h3>Job match</h3>
      <p className="hint">
        What <a href={job.url} target="_top">{job.title} · {job.company}</a> asks for, and whether this CV shows it. Updates as you edit.
      </p>
      <div className="match">
        <div className="match-bar"><i style={{ width: `${pct}%` }} /></div>
        <span><strong>{covered}</strong> of {rows.length} covered · {pct}%</span>
      </div>
      {!rows.length && <p className="hint">No key skills found in the posting.</p>}
      <ul className="kw-list">
        {rows
          .sort((a, b) => Number(a.ok) - Number(b.ok))
          .map(({ k, ok }) => {
            const hit = ok ? null : hiddenHit(k);
            return (
              <li key={k} className={ok ? 'ok' : 'miss'}>
                <span className="kw-mark">{ok ? '✓' : '✗'}</span>
                <span className="kw-name">{k}</span>
                {!ok && hit && (
                  <button title={`Your profile mentions it in a hidden ${hit.what}`} onClick={() => update((d) => void (d.variants[vi].hidden = d.variants[vi].hidden.filter((h) => h !== hit.id)))}>
                    Show hidden {hit.what.split(' ')[0]}
                  </button>
                )}
                {!ok && !hit && (
                  <button title="Only add it if it is true for you" onClick={goToSkills}>
                    Where to add
                  </button>
                )}
              </li>
            );
          })}
      </ul>
      <p className="hint">Only add a skill you really have. To rephrase a bullet towards this job, select it and use ✦ Rewrite → Fit this job.</p>
    </div>
  );
}

// --- History: restore points of your profile, versions and design ------------------------------------------------

interface Snap {
  id: string;
  reason: string;
  created: number;
  versions: string[];
  jobs: number;
  projects: number;
  summary: string;
  free: number;
}

function when(ts: number): string {
  const d = new Date(ts * 1000);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return days === 0 ? `Today ${time}` : days === 1 ? `Yesterday ${time}` : `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`;
}

function HistoryTab({ data }: Props) {
  const [items, setItems] = useState<Snap[] | null>(null);
  const [busy, setBusy] = useState('');
  useEffect(() => {
    fetch(`${API}history`)
      .then((r) => r.json())
      .then((b) => setItems(b.items ?? []))
      .catch(() => setItems([]));
  }, []);
  const restore = async (s: Snap) => {
    if (!confirm(`Restore your CV as it was ${when(s.created).toLowerCase()}?\n\nYour profile text, versions and design go back to that point. The current state is kept as a restore point first, so this can be undone.`)) return;
    setBusy(s.id);
    const res = await fetch(`${API}history/${encodeURIComponent(s.id)}/restore`, { method: 'POST' });
    if (res.ok) location.reload();
    else {
      setBusy('');
      alert((await res.json().catch(() => ({}))).error || 'Could not restore.');
    }
  };
  return (
    <div className="panel">
      <h3>History</h3>
      <p className="hint">
        CVMaker keeps a restore point before your CV changes (at most one per 10 minutes of editing, and always before a version is deleted).
        {data.mode === 'cv' && ' This covers your profile and versions; a tailored CV is restored from its own page.'}
      </p>
      {items === null && <p className="hint">Loading…</p>}
      {items && !items.length && <p className="hint">No restore points yet. The first one is made with your next change.</p>}
      <ul className="history">
        {items?.map((s) => (
          <li key={s.id}>
            <div className="h-top">
              <strong>{when(s.created)}</strong>
              <button disabled={!!busy} onClick={() => restore(s)}>
                {busy === s.id ? 'Restoring…' : 'Restore'}
              </button>
            </div>
            <div className="h-reason">{s.reason}</div>
            <div className="h-meta">
              {s.versions.length > 0 && <span title={s.versions.join('\n')}>{s.versions.length} version{s.versions.length > 1 ? 's' : ''}: {s.versions.join(', ')}</span>}
              <span>{s.jobs} job{s.jobs === 1 ? '' : 's'} · {s.projects} project{s.projects === 1 ? '' : 's'}{s.free ? ` · ${s.free} elements` : ''}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
