// Browser results travel only over the authorized loopback WebSocket.
// Keep each frame well below the server's JSON message limit.
const CHUNK_BYTES = 98_304;
const MAX_RESULT_BYTES = 8_500_000;
// Python's default json.dumps may turn one CJK character into six ASCII
// characters, so even the direct path uses a conservative character bound.
const DIRECT_LIMIT = 100_000;

function toBase64(bytes) {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 8192)
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
  return btoa(binary);
}

export async function encodeResultFrames(result) {
  const json = JSON.stringify(result);
  if (json.length <= DIRECT_LIMIT) return [json];
  const bytes = new TextEncoder().encode(json);
  if (bytes.length > MAX_RESULT_BYTES) throw new Error("result_too_large");
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
    .map((value) => value.toString(16).padStart(2, "0")).join("");
  const total = Math.ceil(bytes.length / CHUNK_BYTES);
  const frames = [];
  for (let index = 0; index < total; index++) {
    frames.push(JSON.stringify({
      type: "result_chunk", id: result.id, index, total, sha256,
      body: toBase64(bytes.subarray(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES)),
    }));
  }
  return frames;
}
