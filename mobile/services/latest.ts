/** "Latest request wins": a slow response must not overwrite state that was produced by something newer
 *  (e.g. a list reload that started before a delete finished must not bring the deleted item back). Pure. */
export function latestOnly() {
  let current = 0;
  return {
    /** Call when a request starts (or when local state changes and older responses should be ignored). */
    next: () => ++current,
    /** True if no newer request or local change has happened since `token` was issued. */
    isCurrent: (token: number) => token === current,
  };
}
