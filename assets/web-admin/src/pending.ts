const savers = new Set<() => Promise<unknown>>();
export function registerSave(save: () => Promise<unknown>) {
  savers.add(save);
  return () => {
    savers.delete(save);
  };
}
export async function flushDrafts() {
  for (const save of savers) await save();
}
