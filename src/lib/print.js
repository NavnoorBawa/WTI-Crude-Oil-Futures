// Print / save as PDF with every figure mounted and every note open. A plain Ctrl/Cmd+P still gets
// the print stylesheet, and "beforeprint" mounts what it can synchronously; this path also waits
// for the figures' chunks to load and for the fonts, so nothing prints as a placeholder.

// Settle time after the last figure mounts, for canvas charts that draw on their next frame.
const SETTLE_MS = 300;
// A figure whose chunk never arrives (offline, a failed deploy) must not block printing forever.
const MOUNT_TIMEOUT_MS = 8000;

const nextFrame = () => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** Resolves once no lazy figure is still loading (LazyMount marks its wrapper aria-busy), or at the timeout. */
export async function figuresMounted(timeoutMs = MOUNT_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (document.querySelector(".lazy-mount[aria-busy='true']") && Date.now() < deadline) {
    await wait(50);
  }
}

/**
 * Mounts every lazy figure ("lazy:mount-all"), opens every note ("notes:open-all"), waits for the
 * figures, the fonts, two frames and a short settle, then opens the browser's print dialog.
 */
export async function printPage() {
  window.dispatchEvent(new Event("lazy:mount-all"));
  window.dispatchEvent(new Event("notes:open-all"));
  await figuresMounted();
  if (document.fonts?.ready) await document.fonts.ready;
  await nextFrame();
  await nextFrame();
  await wait(SETTLE_MS);
  window.print();
}
