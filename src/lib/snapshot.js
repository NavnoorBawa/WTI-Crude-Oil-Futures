import { createContext } from "react";

/**
 * The data snapshot the page shows, for components deep in the tree that need to name it (a CSV
 * file name carries the snapshot date). Provided by Dashboard; the default means "not loaded".
 *
 * @type {import("react").Context<{frozenAt: string|null}>}
 */
export const SnapshotContext = createContext({ frozenAt: null });
