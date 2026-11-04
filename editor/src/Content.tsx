// The Content panel: everything on the CV, section by section - add, remove, reorder and show/hide sections and
// their items, edit skill groups and short lists as chips, the photo, and new sections.
import { useRef, useState, type ReactNode } from 'react';
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { CvData, SectionKey, Variant } from './types';
import { columns, customKey, ordered, sectionTitle } from './model';
import { addSection, addSectionAfter, photoBack, newEdu, newEntry, newJob, newProject, newSkill } from './items';
import { selectorFor, type Sel } from './Toolbar';

type Update = (fn: (d: CvData) => void) => void;
type ItemAction = (what: 'remove' | 'copy' | 'new' | 'up' | 'down' | 'hide', k: string, i?: string) => void;

interface Props {
  data: CvData;
  variant: Variant;
  update: Update;
  setSel: (s: Sel) => void;
  upload: (f: File) => Promise<string>;
  itemAction: ItemAction;
}

const SECTION_PRESETS = ['Certifications', 'Courses', 'Awards', 'Volunteering', 'Publications', 'Conferences & talks', 'Achievements', 'References'];
const LIST_KEYS = { softSkills: 'soft', languages: 'lang', interests: 'interest' } as const;

/** "Java, C++ (11, 17), Go" -> ["Java", "C++ (11, 17)", "Go"]: commas inside brackets don't split. */
export const splitItems = (value: string) => value.split(/,\s*(?![^()]*\))/).map((x) => x.trim()).filter(Boolean);

/** Select something on the page and bring it into view. */
function show(setSel: (s: Sel) => void, k: string, i?: string) {
  setSel({ type: 'el', k, i });
  requestAnimationFrame(() => document.querySelector(selectorFor(k, i))?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
}

function Chips({ items, onRemove, onAdd, placeholder }: { items: string[]; onRemove: (i: number) => void; onAdd: (v: string) => void; placeholder: string }) {
  const [text, setText] = useState('');
  const add = () => {
    for (const v of splitItems(text)) onAdd(v);
    setText('');
  };
  return (
    <div className="chips">
      {items.map((x, i) => (
        <span key={`${x}-${i}`} className="chip">
          {x || <em>empty</em>}
          <button title="Remove" onClick={() => onRemove(i)}>
            ×
          </button>
        </span>
      ))}
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}

function ItemRow({ label, hidden, onClick, onHide, onUp, onDown, onDelete }: {
  label: string; hidden?: boolean; onClick: () => void; onHide?: () => void; onUp?: () => void; onDown?: () => void; onDelete: () => void;
}) {
  return (
    <li className={`c-item ${hidden ? 'is-hidden' : ''}`}>
      <button className="c-label" onClick={onClick} title="Select it on the page">
        {label || <em>Empty</em>}
      </button>
      {onUp && <button className="c-act" title="Move up" onClick={onUp}>↑</button>}
      {onDown && <button className="c-act" title="Move down" onClick={onDown}>↓</button>}
      {onHide && (
        <button className="eye" title="Show or hide in this version" onClick={onHide}>
          {hidden ? 'Hidden' : 'Shown'}
        </button>
      )}
      <button className="c-act del" title="Delete" onClick={onDelete}>
        ×
      </button>
    </li>
  );
}

function SectionRow({ id, title, count, hidden, open, onToggleOpen, onHide, onDelete, deleteTitle, children, extra }: {
  id: string; title: string; count?: number; hidden: boolean; open: boolean; onToggleOpen: () => void; onHide: () => void;
  onDelete?: () => void; deleteTitle?: string; children?: ReactNode; extra?: ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <li ref={setNodeRef} className={`c-sec ${hidden ? 'is-hidden' : ''} ${isDragging ? 'dragging' : ''}`} style={{ transform: CSS.Transform.toString(transform), transition }}>
      <div className="c-sec-head">
        <span className="grip" {...attributes} {...listeners} title="Drag to change the order">
          ⠿
        </span>
        <button className="c-sec-title" onClick={onToggleOpen} aria-expanded={open}>
          <span className="caret">{open ? '▾' : '▸'}</span> {title || <em>Untitled</em>}
          {count !== undefined && <span className="c-count">{count}</span>}
        </button>
        {extra}
        <button className="eye" onClick={onHide} title="Show or hide this section in this version">
          {hidden ? 'Hidden' : 'Shown'}
        </button>
        {onDelete && (
          <button className="c-act del" title={deleteTitle} onClick={onDelete}>
            ×
          </button>
        )}
      </div>
      {open && <div className="c-sec-body">{children}</div>}
    </li>
  );
}

export function ContentPanel({ data, variant, update, setSel, upload, itemAction }: Props) {
  const c = data.content;
  const vi = data.variants.findIndex((v) => v.id === variant.id);
  const setV = (fn: (v: Variant) => void) => update((d) => fn(d.variants[vi]));
  const hidden = new Set(variant.hidden);
  const toggle = (id: string) => setV((v) => void (v.hidden = v.hidden.includes(id) ? v.hidden.filter((x) => x !== id) : [...v.hidden, id]));
  const [open, setOpen] = useState<Set<string>>(() => new Set(['experience', 'skills']));
  const flip = (k: string) => setOpen((o) => new Set(o.has(k) ? [...o].filter((x) => x !== k) : [...o, k]));
  const photoInput = useRef<HTMLInputElement>(null);
  const L = data.design.layout;
  const cols = columns(variant, L.sidebar === 'none' ? { ...L, sidebar: 'right' } : L, c);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 3 } }));

  /** Rows for a list of items with ids (jobs, projects, education, entries). */
  const rows = <T extends { id: string }>(k: string, items: T[], label: (x: T) => string, hideable = true) => (
    <ul className="c-items">
      {items.map((x, n) => (
        <ItemRow
          key={x.id}
          label={label(x)}
          hidden={hidden.has(x.id)}
          onClick={() => show(setSel, k, x.id)}
          onHide={hideable ? () => toggle(x.id) : undefined}
          onUp={n > 0 ? () => itemAction('up', k, x.id) : undefined}
          onDown={n < items.length - 1 ? () => itemAction('down', k, x.id) : undefined}
          onDelete={() => itemAction('remove', k, x.id)}
        />
      ))}
    </ul>
  );
  const addBtn = (label: string, run: (d: CvData) => { k: string; i: string }) => (
    <button
      className="c-add"
      onClick={() => {
        let target: { k: string; i: string } | undefined;
        update((d) => void (target = run(d)));
        if (target) show(setSel, target.k, target.i);
      }}
    >
      + {label}
    </button>
  );
  const listChips = (key: keyof typeof LIST_KEYS, placeholder: string) => (
    <Chips
      items={c[key]}
      placeholder={placeholder}
      onRemove={(n) => update((d) => void d.content[key].splice(n, 1))}
      onAdd={(v) =>
        update((d) => {
          const arr = d.content[key];
          if (arr.length === 1 && !arr[0].trim()) arr[0] = v; // fill the empty placeholder item first
          else arr.push(v);
        })
      }
    />
  );

  const body = (k: SectionKey): { count?: number; node: ReactNode; extra?: ReactNode } => {
    if (k.startsWith('x:')) {
      const si = c.custom.findIndex((s) => customKey(s.id) === k);
      const sec = c.custom[si];
      return {
        count: sec.entries.length,
        node: (
          <>
            <label className="c-rename">
              <span>Heading</span>
              <input value={sec.title} placeholder="Section heading" onChange={(e) => update((d) => void (d.content.custom[si].title = e.target.value))} />
            </label>
            {rows('entry', sec.entries, (e) => [e.title, e.subtitle].filter(Boolean).join(' · '))}
            <div className="c-row">
              {addBtn('Add entry', (d) => {
                const e = newEntry();
                d.content.custom[si].entries.push(e);
                return { k: 'entry', i: e.id };
              })}
              <button className="c-add danger" onClick={() => itemAction('remove', 'section', k)}>
                Delete section
              </button>
            </div>
          </>
        ),
      };
    }
    switch (k) {
      case 'summary':
        return { node: <p className="hint clamp">{c.summary.replace(/\*\*/g, '') || 'Empty: click it on the page to write it.'}</p> };
      case 'experience':
        return {
          count: c.experience.length,
          node: (
            <>
              {rows('job', c.experience, (j) => [j.role, j.company].filter(Boolean).join(' · '))}
              {addBtn('Add job', (d) => {
                const j = newJob();
                d.content.experience.push(j);
                return { k: 'job', i: j.id };
              })}
            </>
          ),
        };
      case 'projects':
        return {
          count: c.projects.length,
          node: (
            <>
              {rows('project', ordered(c.projects, variant.projectOrder), (p) => p.title)}
              {addBtn('Add project', (d) => {
                const p = newProject();
                d.content.projects.push(p);
                return { k: 'project', i: p.id };
              })}
            </>
          ),
        };
      case 'skills':
        return {
          count: c.skills.length,
          node: (
            <>
              {c.skills.map((g, gi) => {
                const own = `skill:${g.id}` in variant.overrides;
                return (
                  <div key={g.id} className={`c-group ${hidden.has(g.id) ? 'is-hidden' : ''}`}>
                    <div className="c-group-head">
                      <input className="c-group-name" value={g.label} placeholder="Group name, e.g. Tools" onChange={(e) => update((d) => void (d.content.skills[gi].label = e.target.value))} />
                      {gi > 0 && <button className="c-act" title="Move up" onClick={() => itemAction('up', 'skill', g.id)}>↑</button>}
                      {gi < c.skills.length - 1 && <button className="c-act" title="Move down" onClick={() => itemAction('down', 'skill', g.id)}>↓</button>}
                      <button className="eye" onClick={() => toggle(g.id)} title="Show or hide this group in this version">
                        {hidden.has(g.id) ? 'Hidden' : 'Shown'}
                      </button>
                      <button className="c-act del" title="Delete the group" onClick={() => itemAction('remove', 'skill', g.id)}>
                        ×
                      </button>
                    </div>
                    <Chips
                      items={splitItems(g.value)}
                      placeholder="Add a skill, Enter"
                      onRemove={(n) => update((d) => void (d.content.skills[gi].value = splitItems(g.value).filter((_, j) => j !== n).join(', ')))}
                      onAdd={(v) => update((d) => void (d.content.skills[gi].value = [...splitItems(d.content.skills[gi].value), v].join(', ')))}
                    />
                    {own && <p className="hint">This version shows its own list for this group (select it on the page to go back to the profile's).</p>}
                  </div>
                );
              })}
              {addBtn('New skill group', (d) => {
                const g = newSkill();
                d.content.skills.push(g);
                return { k: 'skill', i: g.id };
              })}
            </>
          ),
        };
      case 'education':
        return {
          count: c.education.length,
          node: (
            <>
              {rows('edu', c.education, (e) => [e.degree, e.school].filter(Boolean).join(' · '), false)}
              {addBtn('Add education', (d) => {
                const e = newEdu();
                d.content.education.push(e);
                return { k: 'edu', i: e.id };
              })}
            </>
          ),
        };
      case 'contact':
        return {
          count: c.contact.filter((x) => x.trim()).length,
          node: (
            <Chips
              items={c.contact}
              placeholder="Email, phone, link… Enter"
              onRemove={(n) => update((d) => void d.content.contact.splice(n, 1))}
              onAdd={(v) =>
                update((d) => {
                  const empty = d.content.contact.findIndex((x) => !x.trim());
                  if (empty >= 0) d.content.contact[empty] = v;
                  else d.content.contact.push(v);
                })
              }
            />
          ),
        };
      case 'softSkills':
        return { count: c.softSkills.filter((x) => x.trim()).length, node: listChips('softSkills', 'Add one, Enter') };
      case 'languages':
        return { count: c.languages.filter((x) => x.trim()).length, node: listChips('languages', 'e.g. English (C1), Enter') };
      case 'interests':
        return { count: c.interests.filter((x) => x.trim()).length, node: listChips('interests', 'Add one, Enter') };
    }
    return { node: null };
  };

  const column = (title: string, keys: SectionKey[], which: 'main' | 'side') => {
    const onDragEnd = (e: DragEndEvent) => {
      if (!e.over || e.active.id === e.over.id) return;
      const next = arrayMove(keys, keys.indexOf(e.active.id as SectionKey), keys.indexOf(e.over.id as SectionKey));
      setV((v) => void (which === 'main' ? (v.mainOrder = next) : (v.sideOrder = next)));
    };
    return (
      <>
        <h4>{title}</h4>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={keys} strategy={verticalListSortingStrategy}>
            <ul className="c-secs">
              {keys.map((k) => {
                const b = body(k);
                return (
                  <SectionRow
                    key={k}
                    id={k}
                    title={sectionTitle(c, k)}
                    count={b.count}
                    hidden={hidden.has(k)}
                    open={open.has(k)}
                    onToggleOpen={() => flip(k)}
                    onHide={() => toggle(k)}
                    onDelete={() => itemAction('remove', 'section', k)}
                    deleteTitle={k.startsWith('x:') ? 'Delete this section' : 'Take it off the page in every version (its text stays in your profile; Shown brings it back)'}
                  >
                    {b.node}
                  </SectionRow>
                );
              })}
            </ul>
          </SortableContext>
        </DndContext>
        <button
          className="c-add"
          onClick={() => {
            let key: SectionKey | undefined;
            update((d) => {
              key = keys.length ? addSectionAfter(d, keys[keys.length - 1]) : addSection(d, '');
              if (!keys.length && which === 'side') {
                const v = d.variants[vi];
                v.mainOrder = v.mainOrder.filter((x) => x !== key);
                v.sideOrder = [...v.sideOrder, key!];
              }
            });
            if (key) {
              setOpen((o) => new Set([...o, key!]));
              show(setSel, 'section', key);
            }
          }}
        >
          + Your own section
        </button>
      </>
    );
  };

  return (
    <div className="panel content-panel">
      <h3>Content</h3>
      <p className="hint">
        Everything on your CV. Open a section to add, reorder or delete its items; <em>Shown / Hidden</em> is for this version only.
        Click an item to select it on the page (then Del, Ctrl+D, Alt+↑↓ work there too).
      </p>

      <h4>Photo</h4>
      <div className="c-photo">
        {c.photo ? <img src={c.photo} alt="" /> : <div className="c-photo-empty">No photo</div>}
        <div className="c-photo-btns">
          <button onClick={() => photoInput.current?.click()}>{c.photo ? 'Change photo' : 'Add a photo'}</button>
          {c.photo && (
            <button className="danger" onClick={() => itemAction('remove', 'photo')}>
              Remove photo
            </button>
          )}
          {L.photo === 'none' && <span className="hint">Hidden by Layout → Photo: None.</span>}
        </div>
        <input
          ref={photoInput}
          type="file"
          accept="image/*"
          hidden
          onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = '';
            if (!f) return;
            const p = await upload(f);
            update((d) => {
              d.content.photo = p;
              if (d.design.layout.photo === 'none') photoBack(d);
            });
          }}
        />
      </div>

      {column('Main column', cols.main, 'main')}
      {column(L.sidebar === 'none' ? 'Sidebar sections (shown under the main column: no sidebar in this layout)' : 'Sidebar', cols.side, 'side')}
      <p className="hint">Drag ⠿ to reorder. To move a section to the other column, select it on the page and use ⇄ in the toolbar.</p>

      <h4>Add a section</h4>
      <p className="hint">For anything the usual sections don't cover. It's added at the end of the main column.</p>
      <div className="section-presets">
        {[...SECTION_PRESETS, ''].map((t) => (
          <button
            key={t || 'own'}
            onClick={() => {
              let key: SectionKey | undefined;
              update((d) => void (key = addSection(d, t)));
              if (key) {
                setOpen((o) => new Set([...o, key!]));
                show(setSel, 'section', key);
              }
            }}
          >
            + {t || 'Your own…'}
          </button>
        ))}
      </div>
    </div>
  );
}
