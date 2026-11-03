import { useContext, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { Bullet, CvData, SectionKey, Variant } from './types';
import { EditContext, Field } from './Editable';
import { columns, ordered, styleVars, uid } from './model';
import { elementsCss } from './design';
import { contactIcon, iconSvg } from './icons';
import { FreeLayer } from './Free';

export type Update = (fn: (d: CvData) => void) => void;

interface Props {
  data: CvData;
  variant: Variant;
  update: Update;
  upload: (file: File) => Promise<string>;
  onOverflow?: (mm: number) => void;
}

const PX_TO_MM = 25.4 / 96;

function linkFor(text: string): string | undefined {
  const t = text.trim();
  if (/^\S+@\S+\.\S+$/.test(t)) return `mailto:${t}`;
  if (/^\+?[\d\s()-]{7,}$/.test(t)) return `tel:${t.replace(/[^\d+]/g, '')}`;
  if (/^(https?:\/\/)?[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(t)) return t.startsWith('http') ? t : `https://${t}`;
  return undefined;
}

/** Hover toolbar shown on blocks while editing (never in the PDF). */
function Tools({ children, className = '' }: { children: ReactNode; className?: string }) {
  const { editable } = useContext(EditContext);
  return editable ? <div className={`tools ui-only ${className}`}>{children}</div> : null;
}

function AddButton({ label, onClick }: { label: string; onClick: () => void }) {
  const { editable } = useContext(EditContext);
  return editable ? (
    <button className="add-btn ui-only" onClick={onClick}>
      + {label}
    </button>
  ) : null;
}

/** An editable list of strings: Enter adds an item below, Backspace on an empty item removes it. */
function ItemList({ idPrefix, items, set, rich, className, k = 'listItem' }: {
  idPrefix: string;
  items: string[];
  set: (fn: (arr: string[]) => void) => void;
  rich?: boolean;
  className?: string;
  k?: string;
}) {
  const { focus } = useContext(EditContext);
  return (
    <ul className={className}>
      {items.map((text, i) => (
        <li key={i}>
          <Field
            id={`${idPrefix}:${i}`}
            k={k}
            i={`${idPrefix}${i}`}
            value={text}
            rich={rich}
            placeholder="Type…"
            href={rich ? undefined : linkFor(text)}
            onChange={(v) => set((a) => void (a[i] = v))}
            onEnter={() => {
              set((a) => void a.splice(i + 1, 0, ''));
              focus(`${idPrefix}:${i + 1}`);
            }}
            onBackspaceEmpty={
              items.length > 1
                ? () => {
                    set((a) => void a.splice(i, 1));
                    focus(`${idPrefix}:${Math.max(0, i - 1)}`);
                  }
                : undefined
            }
          />
        </li>
      ))}
    </ul>
  );
}

/** Bullets of a job or project. Each can be hidden in this version or keep its own wording here. */
function BulletList({ idPrefix, items, set, className, variant, setV }: {
  idPrefix: string;
  items: Bullet[];
  set: (fn: (arr: Bullet[]) => void) => void;
  className?: string;
  variant: Variant;
  setV: (fn: (v: Variant) => void) => void;
}) {
  const { focus, editable } = useContext(EditContext);
  const [reveal, setReveal] = useState(false);
  const hidden = new Set(variant.hidden);
  const hiddenCount = items.filter((b) => hidden.has(b.id)).length;
  const toggleHidden = (id: string) =>
    setV((v) => void (v.hidden = v.hidden.includes(id) ? v.hidden.filter((x) => x !== id) : [...v.hidden, id]));
  return (
    <>
      <ul className={className}>
        {items.map((b, i) => {
          const isHidden = hidden.has(b.id);
          if (isHidden && !(editable && reveal)) return null;
          const key = `b:${b.id}`;
          const own = key in variant.overrides;
          return (
            <li key={b.id} className={`block bullet ${isHidden ? 'is-hidden ui-only' : ''}`}>
              <Tools className="bullet-tools">
                <button title={isHidden ? 'Show this bullet in this version' : 'Hide this bullet in this version (it stays in your profile)'} onClick={() => toggleHidden(b.id)}>
                  {isHidden ? 'Show' : 'Hide'}
                </button>
                {own ? (
                  <button title="Show the profile's wording again" onClick={() => setV((v) => void delete v.overrides[key])}>Use profile wording</button>
                ) : (
                  <button title="Change the wording for this version only; your profile keeps its text" onClick={() => setV((v) => void (v.overrides[key] = b.text))}>Own wording</button>
                )}
              </Tools>
              <Field
                id={`${idPrefix}:${b.id}`}
                k="bullet"
                i={b.id}
                value={own ? variant.overrides[key] : b.text}
                rich
                className={own ? 'own' : undefined}
                placeholder="Type…"
                onChange={(val) => (own ? setV((v) => void (v.overrides[key] = val)) : set((a) => void (a[i].text = val)))}
                onEnter={() => {
                  const nb = { id: uid('b'), text: '' };
                  set((a) => void a.splice(i + 1, 0, nb));
                  focus(`${idPrefix}:${nb.id}`);
                }}
                onBackspaceEmpty={
                  items.length > 1 && !own
                    ? () => {
                        set((a) => void a.splice(i, 1));
                        const prev = items[Math.max(0, i - 1)];
                        if (prev) focus(`${idPrefix}:${prev.id}`);
                      }
                    : undefined
                }
              />
            </li>
          );
        })}
      </ul>
      {editable && hiddenCount > 0 && (
        <button className="reveal ui-only" onClick={() => setReveal((r) => !r)}>
          {reveal ? 'Hide the hidden bullets again' : `${hiddenCount} bullet${hiddenCount > 1 ? 's' : ''} hidden in this version · show`}
        </button>
      )}
    </>
  );
}

function ImagePick({ src, className, alt, onPick, k, i }: { src: string; className: string; alt: string; onPick: (f: File) => void; k: string; i?: string }) {
  const { editable } = useContext(EditContext);
  const input = useRef<HTMLInputElement>(null);
  if (!src && !editable) return null;
  return (
    <>
      {src ? (
        <img
          src={src}
          data-k={k}
          data-i={i}
          className={className}
          alt={alt}
          title={editable ? 'Double-click to replace the image' : undefined}
          onDoubleClick={() => editable && input.current?.click()}
        />
      ) : (
        <button className={`${className} img-placeholder ui-only`} data-k={k} data-i={i} onClick={() => input.current?.click()}>
          + {alt}
        </button>
      )}
      {editable && (
        <input
          ref={input}
          data-pick={`${k}${i ? `#${i}` : ''}`}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onPick(f);
            e.target.value = '';
          }}
        />
      )}
    </>
  );
}

export function CvPage({ data, variant, update, upload, onOverflow }: Props) {
  const { content: c, style, design } = data;
  const L = design.layout;
  const vIndex = data.variants.findIndex((v) => v.id === variant.id);
  const hidden = new Set(variant.hidden);
  const pageRef = useRef<HTMLDivElement>(null);
  const setV = (fn: (v: Variant) => void) => update((d) => fn(d.variants[vIndex]));
  /** A field that shows this version's own wording when it has one, else the shared text. */
  const own = (key: string, shared: string, setShared: (val: string) => void) => {
    const has = key in variant.overrides;
    return {
      value: has ? variant.overrides[key] : shared,
      className: has ? 'own' : '',
      onChange: (val: string) => (has ? setV((v) => void (v.overrides[key] = val)) : setShared(val)),
    };
  };
  const ownTool = (key: string, shared: string, what: string) =>
    key in variant.overrides ? (
      <button title={`Show the profile's ${what} again`} onClick={() => setV((v) => void delete v.overrides[key])}>Use profile {what}</button>
    ) : (
      <button title={`Change the ${what} for this version only; your profile keeps its text`} onClick={() => setV((v) => void (v.overrides[key] = shared))}>Own {what}</button>
    );

  useLayoutEffect(() => {
    if (!onOverflow || !pageRef.current) return;
    const cols = pageRef.current.querySelectorAll<HTMLElement>('.col');
    let over = 0;
    cols.forEach((col) => (over = Math.max(over, col.scrollHeight - col.clientHeight)));
    onOverflow(over > 1 ? over * PX_TO_MM : 0);
  });

  const heading = (key: SectionKey, col: Column, extra?: ReactNode) => (
    <h2 className="section-title" data-k={col === 'side' ? 'sideHeading' : 'heading'} data-i={key}>
      <Field id={`h:${key}`} value={c.headings[key]} onChange={(v) => update((d) => void (d.content.headings[key] = v))} />
      {extra}
    </h2>
  );

  const hideTool = (id: string, label = 'Hide in this version') => (
    <button title={label} onClick={() => setV((v) => void v.hidden.push(id))}>
      Hide
    </button>
  );

  const photo = L.photo === 'none' ? 'none' : L.photo === 'header' || L.sidebar === 'none' ? 'header' : 'side';
  const photoEl = (
    <ImagePick
      src={c.photo}
      className="photo"
      alt="Photo"
      k="photo"
      onPick={async (f) => {
        const p = await upload(f);
        update((d) => void (d.content.photo = p));
      }}
    />
  );

  const sections: Record<SectionKey, (col: Column) => ReactNode> = {
    summary: (col) => {
      const f = own('summary', c.summary, (val) => update((d) => void (d.content.summary = val)));
      return (
        <section key="summary" className="section block" data-k="section" data-i="summary">
          <Tools>{ownTool('summary', c.summary, 'summary')}</Tools>
          {heading('summary', col)}
          <Field id="summary" k="summary" as="p" rich className={`summary ${f.className}`} value={f.value} onChange={f.onChange} />
        </section>
      );
    },
    experience: (col) => (
      <section key="experience" className="section" data-k="section" data-i="experience">
        {heading('experience', col)}
        {c.experience.map((job, ji) =>
          hidden.has(job.id) ? null : (
            <div key={job.id} className="block job" data-k="job" data-i={job.id}>
              <Tools>
                {hideTool(job.id)}
                <button onClick={() => update((d) => void d.content.experience.splice(ji, 1))}>Delete</button>
              </Tools>
              <div className="job-head">
                <ImagePick
                  src={job.logo}
                  className="job-logo"
                  alt="Logo"
                  k="jobLogo"
                  i={job.id}
                  onPick={async (f) => {
                    const p = await upload(f);
                    update((d) => void (d.content.experience[ji].logo = p));
                  }}
                />
                <div className="job-titles">
                  <Field id={`job:${job.id}:role`} k="jobRole" i={job.id} className="job-role" value={job.role} onChange={(v) => update((d) => void (d.content.experience[ji].role = v))} />
                  <Field id={`job:${job.id}:company`} k="jobCompany" i={job.id} className="job-company" value={job.company} onChange={(v) => update((d) => void (d.content.experience[ji].company = v))} />
                </div>
                <Field id={`job:${job.id}:dates`} k="jobDates" i={job.id} className="job-dates" value={job.dates} onChange={(v) => update((d) => void (d.content.experience[ji].dates = v))} />
              </div>
              <BulletList idPrefix={`job:${job.id}:b`} className="bullets" items={job.bullets} variant={variant} setV={setV} set={(fn) => update((d) => fn(d.content.experience[ji].bullets))} />
            </div>
          ),
        )}
        <AddButton
          label="Add job"
          onClick={() =>
            update((d) =>
              void d.content.experience.push({ id: uid('job'), role: 'Role', company: 'Company', logo: '', dates: 'MM/YYYY – Present', bullets: [{ id: uid('b'), text: 'What you did and the **impact** it had.' }] }),
            )
          }
        />
      </section>
    ),
    projects: (col) => (
      <section key="projects" className="section" data-k="section" data-i="projects">
        {heading(
          'projects',
          col,
          <>
            <span className="heading-sep"> - </span>
            <Field
              id="projectsLink"
              k="projectsLink"
              className="heading-link"
              value={c.projectsLink}
              href={linkFor(c.projectsLink)}
              onChange={(v) => update((d) => void (d.content.projectsLink = v))}
            />
          </>,
        )}
        {ordered(c.projects, variant.projectOrder).map((p) => {
          if (hidden.has(p.id)) return null;
          const pi = c.projects.findIndex((x) => x.id === p.id);
          const tech = own(`tech:${p.id}`, p.tech, (val) => update((d) => void (d.content.projects[pi].tech = val)));
          return (
            <div key={p.id} className="block project" data-k="project" data-i={p.id}>
              <Tools>
                {hideTool(p.id)}
                {ownTool(`tech:${p.id}`, p.tech, 'tech')}
                <button onClick={() => update((d) => void d.content.projects.splice(pi, 1))}>Delete</button>
              </Tools>
              <div className="project-title">
                <Field id={`proj:${p.id}:title`} k="projectTitle" i={p.id} value={p.title} onChange={(v) => update((d) => void (d.content.projects[pi].title = v))} />{' '}
                <Field id={`proj:${p.id}:tech`} k="projectTech" i={p.id} className={`project-tech ${tech.className}`} value={tech.value} placeholder="(tech)" onChange={tech.onChange} />
              </div>
              <BulletList idPrefix={`proj:${p.id}:b`} className="bullets dots" items={p.bullets} variant={variant} setV={setV} set={(fn) => update((d) => fn(d.content.projects[pi].bullets))} />
            </div>
          );
        })}
        <AddButton
          label="Add project"
          onClick={() => update((d) => void d.content.projects.push({ id: uid('proj'), title: 'Project name', tech: '(Tech)', bullets: [{ id: uid('b'), text: 'What it does and **how you built it**.' }] }))}
        />
      </section>
    ),
    contact: (col) => {
      const icon = (text: string, isLocation = false) =>
        L.contactIcons ? <span className="ci" aria-hidden dangerouslySetInnerHTML={{ __html: iconSvg(contactIcon(text, isLocation)) }} /> : null;
      return (
        <section key="contact" className="section" data-k="section" data-i="contact">
          {heading('contact', col)}
          <ul className="side-list contact-list">
            {c.contact.map((item, i) => (
              <li key={i}>
                {icon(item)}
                <Field
                  id={`contact:${i}`}
                  k="contact"
                  i={`c${i}`}
                  value={item}
                  href={linkFor(item)}
                  placeholder="Type…"
                  onChange={(v) => update((d) => void (d.content.contact[i] = v))}
                  onBackspaceEmpty={() => update((d) => void d.content.contact.splice(i, 1))}
                />
              </li>
            ))}
            <li>
              {icon(variant.location, true)}
              <Field id="location" k="contact" i="location" value={variant.location} placeholder="City, Country" onChange={(val) => setV((v) => void (v.location = val))} />
            </li>
          </ul>
          <AddButton label="Add contact line" onClick={() => update((d) => void d.content.contact.push(''))} />
        </section>
      );
    },
    skills: (col) => (
      <section key="skills" className="section" data-k="section" data-i="skills">
        {heading('skills', col)}
        {c.skills.map((g, gi) => {
          if (hidden.has(g.id)) return null;
          const val = own(`skill:${g.id}`, g.value, (v) => update((d) => void (d.content.skills[gi].value = v)));
          return (
            <div key={g.id} className="block skill" data-k="skill" data-i={g.id}>
              <Tools>
                {hideTool(g.id)}
                {ownTool(`skill:${g.id}`, g.value, 'list')}
                <button onClick={() => update((d) => void d.content.skills.splice(gi, 1))}>Delete</button>
              </Tools>
              <Field id={`skill:${g.id}:label`} k="skillLabel" i={g.id} as="div" className="skill-label" value={g.label} onChange={(v) => update((d) => void (d.content.skills[gi].label = v))} />
              <Field id={`skill:${g.id}:value`} k="skillValue" i={g.id} as="div" className={`skill-value ${val.className}`} value={val.value} onChange={val.onChange} />
            </div>
          );
        })}
        <AddButton label="Add skill group" onClick={() => update((d) => void d.content.skills.push({ id: uid('skill'), label: 'Category', value: 'Skill, Skill' }))} />
      </section>
    ),
    softSkills: (col) => (
      <section key="softSkills" className="section" data-k="section" data-i="softSkills">
        {heading('softSkills', col)}
        <ItemList idPrefix="soft" className="side-list" items={c.softSkills} set={(fn) => update((d) => fn(d.content.softSkills))} />
      </section>
    ),
    languages: (col) => (
      <section key="languages" className="section" data-k="section" data-i="languages">
        {heading('languages', col)}
        <ItemList idPrefix="lang" className="side-list" items={c.languages} set={(fn) => update((d) => fn(d.content.languages))} />
      </section>
    ),
    interests: (col) => (
      <section key="interests" className="section" data-k="section" data-i="interests">
        {heading('interests', col)}
        <ItemList idPrefix="interest" className="side-list" items={c.interests} set={(fn) => update((d) => fn(d.content.interests))} />
      </section>
    ),
    education: (col) => (
      <section key="education" className="section" data-k="section" data-i="education">
        {heading('education', col)}
        {c.education.map((e, ei) => {
          const degree = own(`degree:${e.id}`, e.degree, (v) => update((d) => void (d.content.education[ei].degree = v)));
          return (
            <div key={e.id} className="block edu" data-k="edu" data-i={e.id}>
              <Tools>
                {ownTool(`degree:${e.id}`, e.degree, 'wording')}
                <button onClick={() => update((d) => void d.content.education.splice(ei, 1))}>Delete</button>
              </Tools>
              <Field id={`edu:${e.id}:degree`} k="eduDegree" i={e.id} as="div" className={`edu-degree ${degree.className}`} value={degree.value} onChange={degree.onChange} />
              <Field id={`edu:${e.id}:school`} k="eduSchool" i={e.id} as="div" className="edu-school" value={e.school} onChange={(v) => update((d) => void (d.content.education[ei].school = v))} />
            </div>
          );
        })}
        <AddButton label="Add education" onClick={() => update((d) => void d.content.education.push({ id: uid('edu'), degree: 'Degree', school: 'School' }))} />
      </section>
    ),
  };

  const titleField = own('title', c.title, (val) => update((d) => void (d.content.title = val)));
  const cols = columns(variant, L);
  const header = (
    <header className={`cv-header ${photo === 'header' ? 'with-photo' : ''}`} data-k="header">
      {photo === 'header' && <div className="header-photo">{photoEl}</div>}
      <div className="header-text">
        <Field id="name" k="name" as="h1" className="cv-name" value={c.name} onChange={(v) => update((d) => void (d.content.name = v))} />
        <Field id="title" k="title" as="div" className={`cv-title ${titleField.className}`} value={titleField.value} onChange={titleField.onChange} />
      </div>
    </header>
  );
  const free = design.free.filter((f) => !hidden.has(f.id));
  const pageClass = [
    'page',
    `sb-${L.sidebar}`,
    `hd-${L.header}`,
    `hs-${L.headingStyle}`,
    L.bullet !== 'default' ? 'bl-custom' : '',
    L.contactIcons ? 'contact-icons' : '',
  ].join(' ');

  return (
    <div className={pageClass} data-k="page" ref={pageRef} style={styleVars(data.fontPt ? { ...style, fontSize: data.fontPt } : style, L)}>
      <style>{elementsCss(design.elements)}</style>
      <FreeLayer els={free} behind update={update} />
      {L.header === 'full' && header}
      <div className="cols">
        <div className="col main" data-k="mainCol">
          {L.header === 'main' && header}
          {cols.main.map((k) => (hidden.has(k) ? null : sections[k]('main')))}
        </div>
        {L.sidebar !== 'none' && (
          <aside className="col side" data-k="sideCol">
            {photo === 'side' && <div className="photo-wrap">{photoEl}</div>}
            {cols.side.map((k) => (hidden.has(k) ? null : sections[k]('side')))}
          </aside>
        )}
      </div>
      <FreeLayer els={free} update={update} />
    </div>
  );
}

type Column = 'main' | 'side';
