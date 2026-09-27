export const EXTENSION_VERSION = "1.3.4";
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
  );
}
