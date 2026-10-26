/**
 * Copies text to the clipboard. Uses the async Clipboard API where the page may (a secure context
 * with permission), else a selected off-screen textarea and execCommand("copy"), which still works
 * over plain http and in older browsers. Restores the reader's selection and focus afterwards.
 *
 * @param {string} text
 * @returns {Promise<boolean>} whether the text reached the clipboard
 */
export async function copyText(text) {
  const value = String(text ?? "");
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText && globalThis.isSecureContext !== false) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Permission denied or no user activation: fall through to the selection fallback.
    }
  }
  if (typeof document === "undefined" || !document.body) return false;
  const active = document.activeElement;
  const selection = document.getSelection?.();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index)) : [];
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.tabIndex = -1;
  Object.assign(area.style, { position: "fixed", top: "0", left: "-9999px", opacity: "0", pointerEvents: "none" });
  document.body.appendChild(area);
  let copied;
  try {
    area.select();
    area.setSelectionRange(0, value.length);
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  } finally {
    area.remove();
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((range) => selection.addRange(range));
    }
    if (active && typeof active.focus === "function") active.focus({ preventScroll: true });
  }
  return copied;
}
