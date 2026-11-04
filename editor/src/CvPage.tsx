import { Fragment, useContext, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { Bullet, Content, CvData, SectionKey, Variant } from './types';
import { EditContext, Field } from './Editable';
import { columns, customKey, ordered, sectionTitle, styleVars, uid } from './model';
import { elementsCss } from './design';
import { contactIcon, iconSvg } from './icons';
import { FreeLayer } from './Free';
import { addSectionAfter } from './items';

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
function ItemList({ idPrefix, items, set, rich, className, k = 'listItem', placeholder = 'Type…' }: {
  idPrefix: string;
  items: string[];
  set: (fn: (arr: string[]) => void) => void;
  rich?: boolean;
  className?: string;
  k?: string;
  placeholder?: string;
}) {
  const { focus, editable } = useContext(EditContext);
  return (
    <ul className={className}>
      {items.map((text, i) => !editable && !text.trim() ? null : (
        <li key={i}>
          <Field
            id={`${idPrefix}:${i}`}
            k={k}
            i={`${idPrefix}${i}`}
            value={text}
            rich={rich}
            placeholder={placeholder}
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
function BulletList({ idPrefix, items, set, className, variant, setV, placeholder = 'What you did and the result it had' }: {
  placeholder?: string;
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
          if (!editable && !(own ? variant.overrides[key] : b.text).trim()) return null;
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
                placeholder={placeholder}
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
  if (src === 'none' || (!src && !editable)) return null; // 'none': the spot was deleted
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
        <button
          className={`${className} img-placeholder ui-only`}
          data-k={k}
          data-i={i}
          title={`Select it, then “+ Add ${alt.toLowerCase()}” in the toolbar (or double-click)`}
          onDoubleClick={() => input.current?.click()}
        >
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
  const { editable, focus } = useContext(EditContext);
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

  const setHeading = (key: SectionKey, v: string) =>
    update((d) => {
      if (key.startsWith('x:')) {
        const sec = d.content.custom.find((x) => customKey(x.id) === key);
        if (sec) sec.title = v;
      } else d.content.headings[key as keyof Content['headings']] = v;
    });
  const heading = (key: SectionKey, col: Column, extra?: ReactNode) => (
    <h2 className="section-title" data-k={col === 'side' ? 'sideHeading' : 'heading'} data-i={key}>
      <Field
        id={`h:${key}`}
        value={key.startsWith('x:') ? c.custom.find((x) => customKey(x.id) === key)?.title ?? '' : sectionTitle(c, key)}
        placeholder="Type the section's heading"
        onChange={(v) => setHeading(key, v)}
      />
      {extra}
    </h2>
  );
  /** Shown in the editor on a section with nothing in it yet; such sections are left out of the PDF. */
  const emptyNote = (key: SectionKey) =>
    editable && isEmpty(c, variant, key) ? <div className="empty-note ui-only">Empty: fill it in, or it's left out of the PDF</div> : null;

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

  const customSection = (key: SectionKey, col: Column) => {
    const si = c.custom.findIndex((x) => customKey(x.id) === key);
    if (si < 0) return null;
    const sec = c.custom[si];
    const setSec = (fn: (s: Content['custom'][number]) => void) => update((d) => fn(d.content.custom[si]));
    return (
      <section key={key} className="section custom-section" data-k="section" data-i={key}>
        <Tools>
          <button title="Delete this section and everything in it" onClick={() => confirm(`Delete the section “${sec.title || 'Untitled'}”?`) && update((d) => void d.content.custom.splice(si, 1))}>Delete section</button>
        </Tools>
        {heading(key, col)}
        {emptyNote(key)}
        {sec.entries.map((e, ei) => {
          if (hidden.has(e.id) || (!editable && !entryFilled(e))) return null;
          return (
            <div key={e.id} className="block entry" data-k="entry" data-i={e.id}>
              <Tools>
                {hideTool(e.id)}
                <button onClick={() => setSec((s) => void s.entries.splice(ei, 1))}>Delete</button>
              </Tools>
              <div className="entry-head">
                <div className="entry-titles">
                  <Field id={`ent:${e.id}:title`} k="entryTitle" i={e.id} className="entry-title" value={e.title} placeholder="Title (certificate, award, role…)" onChange={(v) => setSec((s) => void (s.entries[ei].title = v))} />
                  <Field id={`ent:${e.id}:sub`} k="entrySubtitle" i={e.id} className="entry-subtitle" value={e.subtitle} placeholder="Issuer or organisation" onChange={(v) => setSec((s) => void (s.entries[ei].subtitle = v))} />
                </div>
                <Field id={`ent:${e.id}:dates`} k="entryDates" i={e.id} className="entry-dates" value={e.dates} placeholder="Date" onChange={(v) => setSec((s) => void (s.entries[ei].dates = v))} />
              </div>
              {(editable || e.bullets.some((b) => b.text.trim())) && (
                e.bullets.length ? (
                  <BulletList idPrefix={`ent:${e.id}:b`} placeholder="Details (optional)" className="bullets" items={e.bullets} variant={variant} setV={setV} set={(fn) => setSec((s) => fn(s.entries[ei].bullets))} />
                ) : (
                  <AddButton label="Add details" onClick={() => setSec((s) => void s.entries[ei].bullets.push({ id: uid('b'), text: '' }))} />
                )
              )}
            </div>
          );
        })}
        <AddButton label="Add entry" onClick={() => setSec((s) => void s.entries.push({ id: uid('ent'), title: '', subtitle: '', dates: '', bullets: [] }))} />
      </section>
    );
  };

  const sections: Record<string, (col: Column) => ReactNode> = {
    summary: (col) => {
      const f = own('summary', c.summary, (val) => update((d) => void (d.content.summary = val)));
      return (
        <section key="summary" className="section block" data-k="section" data-i="summary">
          <Tools>{ownTool('summary', c.summary, 'summary')}</Tools>
          {heading('summary', col)}
          <Field id="summary" k="summary" as="p" rich className={`summary ${f.className}`} value={f.value} placeholder="Two or three sentences: who you are, what you're best at and what you want to do next" onChange={f.onChange} />
        </section>
      );
    },
    experience: (col) => (
      <section key="experience" className="section" data-k="section" data-i="experience">
        {heading('experience', col)}
        {emptyNote('experience')}
        {c.experience.map((job, ji) =>
          hidden.has(job.id) || (!editable && !jobFilled(job)) ? null : (
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
                  <Field id={`job:${job.id}:role`} k="jobRole" i={job.id} className="job-role" value={job.role} placeholder="Job title" onChange={(v) => update((d) => void (d.content.experience[ji].role = v))} />
                  <Field id={`job:${job.id}:company`} k="jobCompany" i={job.id} className="job-company" value={job.company} placeholder="Company" onChange={(v) => update((d) => void (d.content.experience[ji].company = v))} />
                </div>
                <Field id={`job:${job.id}:dates`} k="jobDates" i={job.id} className="job-dates" value={job.dates} placeholder="Start – End" onChange={(v) => update((d) => void (d.content.experience[ji].dates = v))} />
              </div>
              <BulletList idPrefix={`job:${job.id}:b`} className="bullets" items={job.bullets} variant={variant} setV={setV} set={(fn) => update((d) => fn(d.content.experience[ji].bullets))} />
            </div>
          ),
        )}
        <AddButton
          label="Add job"
          onClick={() =>
            update((d) =>
              void d.content.experience.push({ id: uid('job'), role: '', company: '', logo: '', dates: '', bullets: [{ id: uid('b'), text: '' }] }),
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
            {(editable || c.projectsLink.trim()) && <span className="heading-sep"> - </span>}
            <Field
              id="projectsLink"
              k="projectsLink"
              className="heading-link"
              placeholder="link to your work (optional)"
              value={c.projectsLink}
              href={linkFor(c.projectsLink)}
              onChange={(v) => update((d) => void (d.content.projectsLink = v))}
            />
          </>,
        )}
        {emptyNote('projects')}
        {ordered(c.projects, variant.projectOrder).map((p) => {
          if (hidden.has(p.id) || (!editable && !p.title.trim() && !p.bullets.some((b) => b.text.trim()))) return null;
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
                <Field id={`proj:${p.id}:title`} k="projectTitle" i={p.id} value={p.title} placeholder="Project name" onChange={(v) => update((d) => void (d.content.projects[pi].title = v))} />{' '}
                <Field id={`proj:${p.id}:tech`} k="projectTech" i={p.id} className={`project-tech ${tech.className}`} value={tech.value} placeholder="(tools used)" onChange={tech.onChange} />
              </div>
              <BulletList idPrefix={`proj:${p.id}:b`} placeholder="What it is and what you did" className="bullets dots" items={p.bullets} variant={variant} setV={setV} set={(fn) => update((d) => fn(d.content.projects[pi].bullets))} />
            </div>
          );
        })}
        <AddButton
          label="Add project"
          onClick={() => update((d) => void d.content.projects.push({ id: uid('proj'), title: '', tech: '', bullets: [{ id: uid('b'), text: '' }] }))}
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
            {c.contact.map((item, i) => !editable && !item.trim() ? null : (
              <li key={i}>
                {icon(item)}
                <Field
                  id={`contact:${i}`}
                  k="contact"
                  i={`c${i}`}
                  value={item}
                  href={linkFor(item)}
                  placeholder={CONTACT_HINTS[i] ?? 'Email, phone, website…'}
                  onChange={(v) => update((d) => void (d.content.contact[i] = v))}
                  onBackspaceEmpty={() => update((d) => void d.content.contact.splice(i, 1))}
                />
              </li>
            ))}
            {(editable || variant.location.trim()) && <li>
              {icon(variant.location, true)}
              <Field id="location" k="contact" i="location" value={variant.location} placeholder="City, Country" onChange={(val) => setV((v) => void (v.location = val))} />
            </li>}
          </ul>
          <AddButton label="Add contact line" onClick={() => update((d) => void d.content.contact.push(''))} />
        </section>
      );
    },
    skills: (col) => (
      <section key="skills" className="section" data-k="section" data-i="skills">
        {heading('skills', col)}
        {emptyNote('skills')}
        {c.skills.map((g, gi) => {
          if (hidden.has(g.id)) return null;
          const val = own(`skill:${g.id}`, g.value, (v) => update((d) => void (d.content.skills[gi].value = v)));
          if (!editable && !val.value.trim()) return null;
          return (
            <div key={g.id} className="block skill" data-k="skill" data-i={g.id}>
              <Tools>
                {hideTool(g.id)}
                {ownTool(`skill:${g.id}`, g.value, 'list')}
                <button onClick={() => update((d) => void d.content.skills.splice(gi, 1))}>Delete</button>
              </Tools>
              <Field id={`skill:${g.id}:label`} k="skillLabel" i={g.id} as="div" className="skill-label" value={g.label} placeholder="Group, e.g. Tools" onChange={(v) => update((d) => void (d.content.skills[gi].label = v))} />
              <Field id={`skill:${g.id}:value`} k="skillValue" i={g.id} as="div" className={`skill-value ${val.className}`} value={val.value} placeholder="Comma-separated: one, two, three" onChange={val.onChange} />
            </div>
          );
        })}
        <AddButton label="Add skill group" onClick={() => update((d) => void d.content.skills.push({ id: uid('skill'), label: '', value: '' }))} />
      </section>
    ),
    softSkills: (col) => (
      <section key="softSkills" className="section" data-k="section" data-i="softSkills">
        {heading('softSkills', col)}
        <ItemList idPrefix="soft" placeholder="Strength, e.g. teamwork" className="side-list" items={c.softSkills} set={(fn) => update((d) => fn(d.content.softSkills))} />
      </section>
    ),
    languages: (col) => (
      <section key="languages" className="section" data-k="section" data-i="languages">
        {heading('languages', col)}
        <ItemList idPrefix="lang" placeholder="Language and level" className="side-list" items={c.languages} set={(fn) => update((d) => fn(d.content.languages))} />
      </section>
    ),
    interests: (col) => (
      <section key="interests" className="section" data-k="section" data-i="interests">
        {heading('interests', col)}
        <ItemList idPrefix="interest" placeholder="Interest" className="side-list" items={c.interests} set={(fn) => update((d) => fn(d.content.interests))} />
      </section>
    ),
    education: (col) => (
      <section key="education" className="section" data-k="section" data-i="education">
        {heading('education', col)}
        {emptyNote('education')}
        {c.education.map((e, ei) => {
          const degree = own(`degree:${e.id}`, e.degree, (v) => update((d) => void (d.content.education[ei].degree = v)));
          if (!editable && !degree.value.trim() && !e.school.trim()) return null;
          return (
            <div key={e.id} className="block edu" data-k="edu" data-i={e.id}>
              <Tools>
                {ownTool(`degree:${e.id}`, e.degree, 'wording')}
                <button onClick={() => update((d) => void d.content.education.splice(ei, 1))}>Delete</button>
              </Tools>
              <Field id={`edu:${e.id}:degree`} k="eduDegree" i={e.id} as="div" className={`edu-degree ${degree.className}`} value={degree.value} placeholder="Degree or qualification" onChange={degree.onChange} />
              <Field id={`edu:${e.id}:school`} k="eduSchool" i={e.id} as="div" className="edu-school" value={e.school} placeholder="School, years" onChange={(v) => update((d) => void (d.content.education[ei].school = v))} />
            </div>
          );
        })}
        <AddButton label="Add education" onClick={() => update((d) => void d.content.education.push({ id: uid('edu'), degree: '', school: '' }))} />
      </section>
    ),
  };

  const titleField = own('title', c.title, (val) => update((d) => void (d.content.title = val)));
  const cols = columns(variant, L, c);
  const renderSection = (k: SectionKey, col: Column) => {
    if (hidden.has(k) || (!editable && isEmpty(c, variant, k))) return null;
    const el = k.startsWith('x:') ? customSection(k, col) : sections[k as keyof typeof sections]?.(col) ?? null;
    if (!editable || !el) return el;
    return (
      <Fragment key={k}>
        {el}
        <div className="section-after ui-only">
          <button
            title="A new section of your own right below this one (type its heading)"
            onClick={() => {
              let key: SectionKey | undefined;
              update((d) => void (key = addSectionAfter(d, k)));
              if (key) focus(`h:${key}`);
            }}
          >
            + Your own section below
          </button>
        </div>
      </Fragment>
    );
  };
  const header = (
    <header className={`cv-header ${photo === 'header' ? 'with-photo' : ''}`} data-k="header">
      {photo === 'header' && <div className="header-photo">{photoEl}</div>}
      <div className="header-text">
        <Field id="name" k="name" as="h1" className="cv-name" value={c.name} placeholder="Your name" onChange={(v) => update((d) => void (d.content.name = v))} />
        <Field id="title" k="title" as="div" className={`cv-title ${titleField.className}`} value={titleField.value} placeholder="The job you're applying for" onChange={titleField.onChange} />
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
          {cols.main.map((k) => renderSection(k, 'main'))}
        </div>
        {L.sidebar !== 'none' && (
          <aside className="col side" data-k="sideCol">
            {photo === 'side' && <div className="photo-wrap">{photoEl}</div>}
            {cols.side.map((k) => renderSection(k, 'side'))}
          </aside>
        )}
      </div>
      <FreeLayer els={free} update={update} />
    </div>
  );
}

type Column = 'main' | 'side';

const CONTACT_HINTS = ['Email', 'Phone', 'LinkedIn or website'];

const filled = (t: string) => t.replace(/\*\*/g, '').trim().length > 0;
const jobFilled = (j: Content['experience'][number]) => filled(j.role) || filled(j.company) || j.bullets.some((b) => filled(b.text));
const entryFilled = (e: Content['custom'][number]['entries'][number]) =>
  filled(e.title) || filled(e.subtitle) || filled(e.dates) || e.bullets.some((b) => filled(b.text));

/** A section with nothing to show in this version: left out of the PDF, marked in the editor. */
export function isEmpty(c: Content, v: Variant, key: SectionKey): boolean {
  const hidden = new Set(v.hidden);
  const shown = <T extends { id: string }>(items: T[]) => items.filter((x) => !hidden.has(x.id));
  if (key.startsWith('x:')) {
    const sec = c.custom.find((x) => customKey(x.id) === key);
    return !sec || !shown(sec.entries).some(entryFilled);
  }
  switch (key) {
    case 'summary':
      return !filled(v.overrides.summary ?? c.summary);
    case 'experience':
      return !shown(c.experience).some(jobFilled);
    case 'projects':
      return !shown(c.projects).some((p) => filled(p.title) || p.bullets.some((b) => filled(b.text)));
    case 'contact':
      return !c.contact.some(filled) && !filled(v.location);
    case 'skills':
      return !shown(c.skills).some((g) => filled(v.overrides[`skill:${g.id}`] ?? g.value));
    case 'education':
      return !c.education.some((e) => filled(e.degree) || filled(e.school));
    case 'softSkills':
    case 'languages':
    case 'interests':
      return !c[key].some(filled);
  }
  return false;
}
