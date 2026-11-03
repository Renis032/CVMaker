import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import App, { PrintView } from './App';
import { allFontsUrl } from './design';
import './page.css';
import './app.css';

const root = createRoot(document.getElementById('root')!);
const print = window.__CV_PRINT__;
if (print) {
  // CVMaker's PDF renderer: draw synchronously so the page is complete before Chrome prints it.
  document.body.classList.add('printing');
  flushSync(() => root.render(<PrintView data={print} />));
} else {
  // Same theme as the rest of CVMaker: the header's toggle (stored as "theme"), else the system setting.
  const setTheme = (t: string | null) => {
    if (t === 'dark' || t === 'light') document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  };
  try {
    setTheme(localStorage.getItem('theme'));
  } catch {
    /* no storage */
  }
  window.addEventListener('message', (e) => e.origin === location.origin && typeof e.data?.theme === 'string' && setTheme(e.data.theme));
  window.addEventListener('storage', (e) => e.key === 'theme' && setTheme(e.newValue));
  // Every font the editor offers, so the font list shows each in its own face.
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = allFontsUrl();
  document.head.appendChild(link);
  root.render(<App />);
}
