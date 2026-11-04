// Things on the CV you can select and act on as a whole: a job, project, skill group, education entry, entry of a
// custom section, bullet, list item, contact line, custom section, the photo and company logos. One place for
// delete / duplicate / move / add-below, used by the toolbar, the keyboard shortcuts, the right-click menu and the
// Content panel.
import type { Bullet, Content, CustomEntry, CvData, Education, Experience, Project, SectionKey, SkillGroup, Variant } from './types';
import { columns, customKey, sectionTitle, uid } from './model';

const LISTS = { soft: 'softSkills', lang: 'languages', interest: 'interests' } as const;
type ListKey = (typeof LISTS)[keyof typeof LISTS];

/** Text inside an item selects the item when you go one level up (Esc), e.g. a job's role -> the job. */
export const PARENT_KIND: Record<string, string> = {
  jobRole: 'job', jobCompany: 'job', jobDates: 'job', jobLogo: 'job',
  projectTitle: 'project', projectTech: 'project',
  skillLabel: 'skill', skillValue: 'skill',
  eduDegree: 'edu', eduSchool: 'edu',
  entryTitle: 'entry', entrySubtitle: 'entry', entryDates: 'entry',
};

/** Text fields that Del empties (the item they belong to stays). */
export const CLEARABLE = new Set(['header', 'name', 'title', 'summary', 'jobRole', 'jobCompany', 'jobDates', 'projectTitle', 'projectTech',
  'projectsLink', 'skillLabel', 'skillValue', 'eduDegree', 'eduSchool', 'entryTitle', 'entrySubtitle', 'entryDates']);

export const ITEM_NAMES: Record<string, string> = {
  job: 'job', project: 'project', skill: 'skill group', edu: 'education entry', entry: 'entry', bullet: 'bullet',
  listItem: 'item', contact: 'contact line', section: 'section', photo: 'photo', jobLogo: 'logo',
};

/** A job's logo spot that was deleted (no logo and no "+ Logo" spot for that job). */
export const NO_LOGO = 'none';

export const newJob = (): Experience => ({ id: uid('job'), role: '', company: '', logo: '', dates: '', bullets: [newBullet()] });
export const newProject = (): Project => ({ id: uid('proj'), title: '', tech: '', bullets: [newBullet()] });
export const newSkill = (): SkillGroup => ({ id: uid('skill'), label: '', value: '' });
export const newEdu = (): Education => ({ id: uid('edu'), degree: '', school: '' });
export const newEntry = (): CustomEntry => ({ id: uid('ent'), title: '', subtitle: '', dates: '', bullets: [] });
export const newBullet = (): Bullet => ({ id: uid('b'), text: '' });

/** The list an item lives in and where, so it can be removed, copied or moved. Works on a draft inside update(). */
interface Found {
  arr: unknown[];
  index: number;
  make: () => unknown; // a new, empty item of the same kind
  copy: () => unknown; // a copy with fresh ids
  /** The id to select / focus after adding or copying (string lists have none). */
  idOf?: (x: unknown) => string;
}

const freshBullets = (bs: Bullet[]) => bs.map((b) => ({ ...b, id: uid('b') }));

export function find(d: CvData, k: string, i?: string): Found | null {
  const c = d.content;
  const byId = <T extends { id: string }>(arr: T[], make: () => T, copy: (x: T) => T): Found | null => {
    const index = arr.findIndex((x) => x.id === i);
    return index < 0 ? null : { arr, index, make, copy: () => copy(arr[index]), idOf: (x) => (x as T).id };
  };
  switch (k) {
    case 'job':
      return byId(c.experience, newJob, (x) => ({ ...structuredClone(x), id: uid('job'), bullets: freshBullets(x.bullets) }));
    case 'project':
      return byId(c.projects, newProject, (x) => ({ ...structuredClone(x), id: uid('proj'), bullets: freshBullets(x.bullets) }));
    case 'skill':
      return byId(c.skills, newSkill, (x) => ({ ...x, id: uid('skill') }));
    case 'edu':
      return byId(c.education, newEdu, (x) => ({ ...x, id: uid('edu') }));
    case 'entry':
      for (const s of c.custom) {
        const f = byId(s.entries, newEntry, (x) => ({ ...structuredClone(x), id: uid('ent'), bullets: freshBullets(x.bullets) }));
        if (f) return f;
      }
      return null;
    case 'bullet':
      for (const owner of [...c.experience, ...c.projects, ...c.custom.flatMap((s) => s.entries)]) {
        const f = byId(owner.bullets, newBullet, (x) => ({ ...x, id: uid('b') }));
        if (f) return f;
      }
      return null;
    case 'section': {
      const index = c.custom.findIndex((s) => customKey(s.id) === i);
      if (index < 0) return null;
      return {
        arr: c.custom, index,
        make: () => ({ id: uid('sec'), title: '', entries: [newEntry()] }),
        copy: () => ({ ...structuredClone(c.custom[index]), id: uid('sec'), entries: c.custom[index].entries.map((e) => ({ ...structuredClone(e), id: uid('ent'), bullets: freshBullets(e.bullets) })) }),
        idOf: (x) => customKey((x as { id: string }).id),
      };
    }
    case 'listItem': {
      const m = /^(soft|lang|interest)(\d+)$/.exec(i ?? '');
      if (!m) return null;
      const arr = c[LISTS[m[1] as keyof typeof LISTS] as ListKey];
      const index = Number(m[2]);
      return index < arr.length ? { arr, index, make: () => '', copy: () => arr[index] } : null;
    }
    case 'contact': {
      const m = /^c(\d+)$/.exec(i ?? '');
      if (!m || Number(m[1]) >= c.contact.length) return null;
      const index = Number(m[1]);
      return { arr: c.contact, index, make: () => '', copy: () => c.contact[index] };
    }
  }
  return null;
}

/** A section heading stands for its section: deleting the heading deletes (or removes) the section. */
export function asSection(k: string, i?: string): [string, string | undefined] {
  return k === 'heading' || k === 'sideHeading' ? ['section', i] : [k, i];
}

/** What can be done with the selected element as a whole. */
export function caps(d: CvData, k0: string, i0?: string) {
  const [k, i] = asSection(k0, i0);
  const f = find(d, k, i);
  const builtinSection = k === 'section' && !!i && !i.startsWith('x:');
  const clearable = CLEARABLE.has(k) || (k === 'contact' && i === 'location');
  return {
    item: !!f || k === 'photo' || k === 'jobLogo' || builtinSection,
    remove:
      !!f || builtinSection || clearable || (k === 'sideCol' && d.design.layout.sidebar !== 'none') ||
      (k === 'photo' && (!!d.content.photo || d.design.layout.photo !== 'none')) ||
      (k === 'jobLogo' && d.content.experience.find((j) => j.id === i)?.logo !== NO_LOGO),
    clear: clearable,
    duplicate: !!f && k !== 'contact',
    move: !!f && f.arr.length > 1,
    addBelow: !!f,
    builtinSection,
    name: ITEM_NAMES[k] ?? 'item',
  };
}

/** Empty a text field (this version's own wording when it has one). */
export function clearField(d: CvData, v: Variant, k: string, i?: string): boolean {
  const c = d.content;
  const own = (key: string) => (key in v.overrides ? ((v.overrides[key] = ''), true) : false);
  const job = c.experience.find((x) => x.id === i);
  const proj = c.projects.find((x) => x.id === i);
  const skill = c.skills.find((x) => x.id === i);
  const edu = c.education.find((x) => x.id === i);
  const entry = c.custom.flatMap((s) => s.entries).find((x) => x.id === i);
  switch (k) {
    case 'contact': if (i === 'location') v.location = ''; return i === 'location';
    case 'header':
      c.name = '';
      if (!own('title')) c.title = '';
      return true;
    case 'name': c.name = ''; return true;
    case 'title': if (!own('title')) c.title = ''; return true;
    case 'summary': if (!own('summary')) c.summary = ''; return true;
    case 'projectsLink': c.projectsLink = ''; return true;
    case 'jobRole': if (job) job.role = ''; return !!job;
    case 'jobCompany': if (job) job.company = ''; return !!job;
    case 'jobDates': if (job) job.dates = ''; return !!job;
    case 'projectTitle': if (proj) proj.title = ''; return !!proj;
    case 'projectTech': if (proj && !own(`tech:${proj.id}`)) proj.tech = ''; return !!proj;
    case 'skillLabel': if (skill) skill.label = ''; return !!skill;
    case 'skillValue': if (skill && !own(`skill:${skill.id}`)) skill.value = ''; return !!skill;
    case 'eduDegree': if (edu && !own(`degree:${edu.id}`)) edu.degree = ''; return !!edu;
    case 'eduSchool': if (edu) edu.school = ''; return !!edu;
    case 'entryTitle': if (entry) entry.title = ''; return !!entry;
    case 'entrySubtitle': if (entry) entry.subtitle = ''; return !!entry;
    case 'entryDates': if (entry) entry.dates = ''; return !!entry;
  }
  return false;
}

/** Take a built-in section off the page in every version (its content stays in the profile; Content shows it again). */
export function removeBuiltinSection(d: CvData, key: string) {
  for (const v of d.variants) if (!v.hidden.includes(key)) v.hidden.push(key);
}

/** A new custom section placed right after `after`, in the same column, in every version. Returns its key. */
export function addSectionAfter(d: CvData, after: SectionKey, title = ''): SectionKey {
  const key = addSection(d, title);
  for (const v of d.variants) {
    const cols = columns(v, d.design.layout.sidebar === 'none' ? { ...d.design.layout, sidebar: 'right' } : d.design.layout, d.content);
    for (const list of [cols.main, cols.side]) {
      const at = list.indexOf(key);
      if (at >= 0) list.splice(at, 1);
    }
    const target = cols.side.includes(after) ? cols.side : cols.main;
    const at = target.indexOf(after);
    target.splice(at < 0 ? target.length : at + 1, 0, key);
    v.mainOrder = cols.main;
    v.sideOrder = cols.side;
  }
  return key;
}

/** Describes what was removed, for the "Deleted … · Undo" notice. */
export function describe(c: Content, k0: string, i0?: string): string {
  const [k, i] = asSection(k0, i0);
  if (k === 'section' && i) return `the “${sectionTitle(c, i as SectionKey) || 'Untitled'}” section`;
  if (k === 'job') {
    const j = c.experience.find((x) => x.id === i);
    return j && (j.role || j.company) ? `the job “${[j.role, j.company].filter(Boolean).join(' · ')}”` : 'the job';
  }
  return `the ${ITEM_NAMES[k] ?? 'item'}`;
}

/** Remove the item. Returns false when there was nothing to remove. */
export function removeItem(d: CvData, k: string, i?: string): boolean {
  if (k === 'photo') {
    // The photo and its spot: the page has no photo at all (Content → Add a photo brings it back).
    d.content.photo = '';
    if (d.design.layout.photo !== 'none') d.design.layout.photoWas = d.design.layout.photo;
    d.design.layout.photo = 'none';
    return true;
  }
  if (k === 'jobLogo') {
    const j = d.content.experience.find((x) => x.id === i);
    if (!j || j.logo === NO_LOGO) return false;
    j.logo = NO_LOGO; // no logo and no "+ Logo" spot for this job (the job's toolbar has "+ Logo" to bring it back)
    return true;
  }
  const f = find(d, k, i);
  if (!f) return false;
  f.arr.splice(f.index, 1);
  return true;
}

/** Insert a copy (or a new empty item) right below; returns the new item's id for selecting it. */
export function insertBelow(d: CvData, k: string, i: string | undefined, how: 'copy' | 'new'): string | undefined {
  const f = find(d, k, i);
  if (!f) return undefined;
  const item = how === 'copy' ? f.copy() : f.make();
  f.arr.splice(f.index + 1, 0, item);
  if (f.idOf) return f.idOf(item);
  return k === 'listItem' ? i!.replace(/\d+$/, String(f.index + 1)) : `c${f.index + 1}`;
}

/** Move the item up or down in its list. Returns the item's id afterwards (string lists change index). */
export function moveItem(d: CvData, k: string, i: string | undefined, dir: -1 | 1): string | undefined {
  const f = find(d, k, i);
  if (!f) return i;
  const j = f.index + dir;
  if (j < 0 || j >= f.arr.length) return i;
  [f.arr[f.index], f.arr[j]] = [f.arr[j], f.arr[f.index]];
  if (k === 'listItem') return i!.replace(/\d+$/, String(j));
  if (k === 'contact') return `c${j}`;
  return i;
}

export function toggleHidden(v: Variant, id: string) {
  v.hidden = v.hidden.includes(id) ? v.hidden.filter((x) => x !== id) : [...v.hidden, id];
}

/** Where the photo goes when it comes back: where it was before it was deleted, else by the layout. */
export function photoBack(d: CvData) {
  const L = d.design.layout;
  L.photo = L.photoWas ?? (L.header === 'full' || L.sidebar === 'none' ? 'header' : 'side');
}

/** A new custom section at the end of the main column, with one empty entry. Returns its section key. */
export function addSection(d: CvData, title: string): SectionKey {
  const sec = { id: uid('sec'), title, entries: [newEntry()] };
  d.content.custom.push(sec);
  return customKey(sec.id);
}
