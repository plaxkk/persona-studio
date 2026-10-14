export const EXTENSION_VERSION = "1.4.0";
export const EXTENSION_DOWNLOAD = "/downloads/persona-studio-browser.zip";
export function extensionNeedsUpdate(version?: string) {
  if (!version || !/^\d+\.\d+\.\d+$/.test(version)) return true;
  const actual = version.split(".").map(Number);
  const wanted = EXTENSION_VERSION.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (actual[i] !== wanted[i]) return actual[i] < wanted[i];
  }
  return false;
}
export function isUpgradeRelatedRoute(path: string) {
  return (
    /^\/engines\/codex\/(verify|select)$/.test(path) ||
    /^\/persona-imports(?:\/|$)/.test(path)
    || /^\/creations(?:\/|$)/.test(path)
    || /^\/interactions(?:\/|$)/.test(path)
    || /^\/growth(?:\/|$)/.test(path)
    || /^\/analytics\/content(?:\/|$)/.test(path)
    || /^\/analytics\/overview(?:\/|$)/.test(path)
    || /^\/drafts\/[a-f0-9-]+\/metrics(?:\/|$)/.test(path)
    || /^\/reports\/weekly(?:\/|$)/.test(path)
    || /^\/benchmarks(?:\/|$)/.test(path)
    // 06C and 06D added route families of their own. They belong here too: an
    // out-of-date connector 403s on them, and without an entry the panel shows
    // the generic "connection lost" line instead of telling the reader to
    // update the connector.
    || /^\/accounts(?:\/|$)/.test(path)
    || /^\/algorithm(?:\/|$)/.test(path)
  );
}
