import { useEffect, useRef, useState } from "react";
import { Download, Eye, EyeOff, Paperclip, Trash2, Upload } from "lucide-react";
import { Button } from "./UI";
import {
  MEDIA_LABELS,
  MEDIA_LIMIT,
  MEDIA_MAX_BYTES,
  downloadMedia,
  fileType,
  humanSize,
  kindOf,
  listMedia,
  mediaBlob,
  removeMedia,
  uploadMedia,
  type Media,
} from "../media";

// Images this small are worth showing inline without asking; a video or a large
// photo is fetched only when the user opens it, since bytes cross the local
// bridge one 384 KB chunk at a time.
const INLINE_BYTES = 4 * 1024 * 1024;

function Preview({
  creationId,
  item,
  open,
}: {
  creationId: string;
  item: Media;
  open: boolean;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const urlRef = useRef("");
  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );
  useEffect(() => {
    if (!open || url) return;
    let disposed = false;
    setBusy(true);
    setError("");
    mediaBlob(creationId, item)
      .then((blob) => {
        if (disposed) return;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = URL.createObjectURL(blob);
        setUrl(urlRef.current);
      })
      .catch((e: Error) => !disposed && setError(e.message))
      .finally(() => !disposed && setBusy(false));
    return () => {
      disposed = true;
    };
  }, [open, url, creationId, item]);
  if (!open) return null;
  if (error)
    return (
      <p role="alert" className="muted">
        {error}
      </p>
    );
  if (busy || !url)
    return (
      <p className="muted">
        {item.kind === "video" ? "正在读取视频" : "正在读取图片"} ·{" "}
        {humanSize(item.bytes)}，请稍候…
      </p>
    );
  return item.kind === "video" ? (
    <video className="creation-media-preview" src={url} controls preload="metadata" />
  ) : (
    <img className="creation-media-preview" src={url} alt={item.filename} />
  );
}

export default function CreationMedia({
  creationId,
  actions,
}: {
  creationId: string;
  actions: { notify: (text: string, error?: boolean) => void };
}) {
  const [items, setItems] = useState<Media[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [shown, setShown] = useState<string[]>([]);
  const [confirming, setConfirming] = useState("");
  const input = useRef<HTMLInputElement | null>(null);

  async function load() {
    setItems(await listMedia(creationId));
    setLoaded(true);
  }
  useEffect(() => {
    void load().catch((e: Error) => setError(e.message));
  }, [creationId]);

  async function add(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      const chosen = [...files];
      let count = items.length;
      let video = items.some((item) => item.kind === "video");
      for (const [index, file] of chosen.entries()) {
        const mime = fileType(file);
        const kind = kindOf(mime);
        if (!kind)
          throw new Error(
            `${file.name}：只支持 PNG、JPEG、WebP、GIF 图片和 MP4、MOV、WebM 视频。`,
          );
        if (!file.size) throw new Error(`${file.name}：这个文件是空的，请重新选择。`);
        if (file.size > MEDIA_MAX_BYTES)
          throw new Error(
            `${file.name}：单个附件请控制在 ${MEDIA_MAX_BYTES / (1024 * 1024)} MB 以内。`,
          );
        if (count >= MEDIA_LIMIT)
          throw new Error(`每条创作最多 ${MEDIA_LIMIT} 个附件，请先删除一个。`);
        if (kind === "video" && video)
          throw new Error("一条创作只放一个视频，请先删除现有视频。");
        setStatus(`正在上传 ${index + 1}/${chosen.length}：${file.name}`);
        const saved = await uploadMedia(creationId, file, (sent, total) =>
          setStatus(
            `正在上传 ${index + 1}/${chosen.length}：${file.name} · ${Math.round((sent / total) * 100)}%`,
          ),
        );
        count += 1;
        if (kind === "video") video = true;
        setItems((current) => [...current, saved]);
        if (saved.kind === "image" && saved.bytes <= INLINE_BYTES)
          setShown((current) => [...current, saved.id]);
      }
      actions.notify(
        chosen.length > 1 ? `已添加 ${chosen.length} 个附件` : "附件已添加",
      );
    } catch (e) {
      setError((e as Error).message);
      await load().catch(() => undefined);
    } finally {
      setBusy(false);
      setStatus("");
      if (input.current) input.current.value = "";
    }
  }

  async function drop(item: Media) {
    setBusy(true);
    setError("");
    try {
      await removeMedia(creationId, item.id);
      setItems((current) => current.filter((row) => row.id !== item.id));
      setConfirming("");
      actions.notify("附件已删除");
    } catch (e) {
      setError((e as Error).message);
      await load().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function save(item: Media) {
    setError("");
    try {
      await downloadMedia(creationId, item);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const full = items.length >= MEDIA_LIMIT;
  return (
    <section className="creation-media">
      <div className="section-title">
        <h3>
          <Paperclip size={16} /> 配图与视频
        </h3>
        <span className="muted">
          {items.length}/{MEDIA_LIMIT}
        </span>
      </div>
      <p className="muted">
        只作为发布时的素材参考，保存在本机，不会自动发到 X。正文定稿后仍可继续添加或删除。
      </p>
      {error && (
        <p role="alert" className="creation-media-error">
          {error}
        </p>
      )}
      {status && <p className="muted">{status}</p>}
      <div className="creation-media-list">
        {items.map((item) => (
          <article className="creation-media-item" key={item.id}>
            <div className="creation-media-head">
              <strong>{item.filename}</strong>
              <small className="muted">
                {MEDIA_LABELS[item.kind] || item.kind} · {humanSize(item.bytes)}
              </small>
            </div>
            <Preview
              creationId={creationId}
              item={item}
              open={shown.includes(item.id)}
            />
            <div className="actions">
              <Button
                disabled={busy}
                onClick={() =>
                  setShown((current) =>
                    current.includes(item.id)
                      ? current.filter((id) => id !== item.id)
                      : [...current, item.id],
                  )
                }
              >
                {shown.includes(item.id) ? <EyeOff size={16} /> : <Eye size={16} />}
                {shown.includes(item.id) ? "收起预览" : "查看预览"}
              </Button>
              <Button disabled={busy} onClick={() => void save(item)}>
                <Download size={16} /> 下载
              </Button>
              {confirming === item.id ? (
                <>
                  <Button disabled={busy} onClick={() => setConfirming("")}>
                    不删了
                  </Button>
                  <Button tone="primary" busy={busy} onClick={() => void drop(item)}>
                    确认删除
                  </Button>
                </>
              ) : (
                <Button disabled={busy} onClick={() => setConfirming(item.id)}>
                  <Trash2 size={16} /> 删除
                </Button>
              )}
            </div>
          </article>
        ))}
      </div>
      {loaded && !items.length && (
        <p className="muted">还没有附件。可以放 1 张主图，或多张轮播图；视频一次一个。</p>
      )}
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/webm"
        onChange={(e) => void add(e.target.files)}
      />
      <div className="actions">
        <Button
          disabled={busy || full}
          busy={busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={16} /> {full ? `已放满 ${MEDIA_LIMIT} 个` : "添加图片或视频"}
        </Button>
      </div>
    </section>
  );
}
