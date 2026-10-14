import { api } from "./api";

// Attachments are reference material for manual publishing; the studio never
// uploads them to X. Bytes travel in fixed-size chunks in both directions
// because the local bridge caps a request body at 1.2 MB and a WebSocket frame
// at 950 KB — a phone photo already strains the first, and a short video never
// fits. The server verifies the sha256 computed here against what it assembled.
export type Media = {
  id: string;
  creation_id: string;
  draft_id: string | null;
  kind: string;
  filename: string;
  mime: string;
  bytes: number;
  sha256: string;
  created: number;
};
type Started = {
  id: string;
  kind: string;
  filename: string;
  mime: string;
  bytes: number;
  chunk_size: number;
  chunks: number;
};
type Block = {
  id: string;
  index: number;
  chunks: number;
  bytes: number;
  filename: string;
  mime: string;
  kind: string;
  data: string;
};

export const MEDIA_LIMIT = 4;
export const MEDIA_MAX_BYTES = 200 * 1024 * 1024;
export const MEDIA_LABELS: Record<string, string> = { image: "图片", video: "视频" };
const MEDIA_TYPES: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
  mp4: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
};
const KINDS: Record<string, string> = {
  "image/png": "image",
  "image/jpeg": "image",
  "image/webp": "image",
  "image/gif": "image",
  "video/mp4": "video",
  "video/quicktime": "video",
  "video/webm": "video",
};

export function fileType(file: File): string {
  const declared = (file.type || "").split(";")[0].trim().toLowerCase();
  if (KINDS[declared]) return declared;
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  return MEDIA_TYPES[extension] || declared;
}

export function kindOf(mime: string): string {
  return KINDS[mime] || "";
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function base64(bytes: Uint8Array): string {
  let text = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    text += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(text);
}

function unbase64(value: string): Uint8Array {
  const text = atob(value);
  const bytes = new Uint8Array(text.length);
  for (let index = 0; index < text.length; index++)
    bytes[index] = text.charCodeAt(index);
  return bytes;
}

async function digest(bytes: Uint8Array): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return [...new Uint8Array(hash)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function listMedia(creationId: string): Promise<Media[]> {
  return api<Media[]>(`/creations/${creationId}/media`);
}

export async function uploadMedia(
  creationId: string,
  file: File,
  onProgress?: (sent: number, total: number) => void,
): Promise<Media> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const root = `/creations/${creationId}/media`;
  const started = await api<Started>(root, "POST", {
    filename: file.name,
    mime: fileType(file),
    bytes: bytes.length,
    sha256: await digest(bytes),
  });
  for (let index = 0; index < started.chunks; index++) {
    const block = bytes.subarray(
      index * started.chunk_size,
      (index + 1) * started.chunk_size,
    );
    await api(`${root}/${started.id}/chunks`, "POST", {
      index,
      data: base64(block),
    });
    onProgress?.(Math.min((index + 1) * started.chunk_size, bytes.length), bytes.length);
  }
  return api<Media>(`${root}/${started.id}/complete`, "POST", {});
}

export function removeMedia(creationId: string, mediaId: string) {
  return api(`/creations/${creationId}/media/${mediaId}`, "DELETE");
}

export async function mediaBlob(
  creationId: string,
  item: { id: string; mime: string },
): Promise<Blob> {
  const parts: BlobPart[] = [];
  for (let index = 0; ; index++) {
    const block = await api<Block>(
      `/creations/${creationId}/media/${item.id}/chunks/${index}`,
    );
    parts.push(unbase64(block.data) as BlobPart);
    if (index + 1 >= block.chunks) break;
  }
  return new Blob(parts, { type: item.mime });
}

export async function downloadMedia(creationId: string, item: Media) {
  const url = URL.createObjectURL(await mediaBlob(creationId, item));
  const link = document.createElement("a");
  link.href = url;
  link.download = item.filename || "附件";
  link.click();
  URL.revokeObjectURL(url);
}
