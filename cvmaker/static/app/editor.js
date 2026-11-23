// Keep the editor's theme in step with the header's toggle (the editor also follows the system setting).
const frame = document.querySelector(".editor-frame");
const sendTheme = () => frame.contentWindow?.postMessage({ theme: document.documentElement.dataset.theme || "" }, location.origin);
new MutationObserver(sendTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
frame.addEventListener("load", sendTheme);
