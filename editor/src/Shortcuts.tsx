// The keyboard shortcuts, shown with "?" or the ⌨ button in the top bar.
const GROUPS: [string, [string, string][]][] = [
  [
    'Anything on the page',
    [
      ['Click', 'Select it (Del deletes it)'],
      ['Drag', 'Move it anywhere on the page (the rest stays put; Alt: no snapping back to its place)'],
      ['Arrows (Shift: 5 mm)', 'Nudge what is selected'],
      ['Click again / double-click / Enter / just type', 'Edit the selected text'],
      ['Drag on an empty spot', 'Selection box: selects everything it touches (whole sections only when fully inside)'],
      ['Shift+click', 'Add to or remove from the selection'],
      ['Esc', 'Select the box around it (role → job → section → page), then nothing'],
      ['Ctrl+Z / Ctrl+Shift+Z', 'Undo / redo'],
      ['?', 'Show or hide this list'],
    ],
  ],
  [
    'A selected item (job, project, skill group, education, entry, bullet, list item, contact line, section)',
    [
      ['Del / Backspace', 'Delete it. Text fields are emptied, built-in sections and the sidebar are taken off the page, the photo or a logo is removed'],
      ['Ctrl+D', 'Duplicate it'],
      ['Ctrl+Enter', 'Add a new, empty one below'],
      ['Alt+↑ / Alt+↓', 'Move it up / down'],
      ['H', 'Hide it in this version (it stays in your profile; bullets: toolbar or right-click)'],
    ],
  ],
  [
    'Several selected',
    [
      ['Del', 'Delete them all'],
      ['H', 'Hide them in this version'],
      ['Arrows', 'Nudge the free objects among them (drag one to move them together)'],
      ['Esc', 'Clear the selection'],
    ],
  ],
  [
    'While typing in a list or bullets',
    [
      ['Enter', 'New bullet / item below'],
      ['Backspace in an empty one', 'Remove it'],
      ['Ctrl+B', 'Bold (bullets and summary)'],
      ['Esc', 'Stop typing (it stays selected: Del now deletes it)'],
    ],
  ],
  [
    'Text boxes, shapes, lines, icons, images',
    [
      ['Del', 'Delete'],
      ['Ctrl+D · Ctrl+C · Ctrl+X · Ctrl+V', 'Duplicate · copy · cut · paste'],
      ['Arrows (Shift: 5 mm)', 'Nudge'],
      ['Ctrl+] / Ctrl+[', 'Forward / backward (Shift: to front / back)'],
      ['Alt while dragging', 'No snapping'],
      ['Enter / double-click', 'Edit a text box'],
    ],
  ],
  [
    'Styles',
    [
      ['Ctrl+Alt+C / Ctrl+Alt+V', 'Copy / paste the style of what is selected'],
    ],
  ],
];

export function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <div className="keys-back" onPointerDown={onClose}>
      <div className="keys" role="dialog" aria-label="Keyboard shortcuts" onPointerDown={(e) => e.stopPropagation()}>
        <div className="keys-head">
          <strong>Keyboard shortcuts</strong>
          <button className="ghost" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>
        {GROUPS.map(([title, rows]) => (
          <section key={title}>
            <h4>{title}</h4>
            <dl>
              {rows.map(([k, what]) => (
                <div key={k}>
                  <dt>
                    {k.split(' / ').map((part, i) => (
                      <span key={i}>
                        {i > 0 && ' / '}
                        <kbd>{part}</kbd>
                      </span>
                    ))}
                  </dt>
                  <dd>{what}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  );
}
