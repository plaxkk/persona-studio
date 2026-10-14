"""Chunked image and video attachments for creation records.

The cloud panel only reaches this API through the connection assistant, and two
hard caps sit on that path: the HTTP boundary rejects request bodies over
1_200_000 bytes (``studio/api.py``) and the bridge closes the socket on frames
over 950_000 bytes (``studio/persona_routes.py``). A phone photo already strains
the first; a short video never fits. So bytes travel in fixed-size chunks in
both directions, and the client verifies what it sent by handing in the sha256
it computed itself.

Staging lives on disk rather than in the database: an upload that is abandoned
halfway must not leave a row claiming to be a usable attachment. The sidecar
JSON holds the declared metadata until ``complete`` promotes the bytes into
``creation_media``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import time
import uuid
from math import ceil
from pathlib import Path

from fastapi import HTTPException

CHUNK = 384 * 1024
MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 4
MAX_VIDEOS = 1
STALE_AFTER = 24 * 3600
IDENT = re.compile(r"^[a-f0-9]{32}$")
MIME_KINDS = {
    "image/png": ("image", ".png"),
    "image/jpeg": ("image", ".jpg"),
    "image/webp": ("image", ".webp"),
    "image/gif": ("image", ".gif"),
    "video/mp4": ("video", ".mp4"),
    "video/quicktime": ("video", ".mov"),
    "video/webm": ("video", ".webm"),
}
MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/x-png": "image/png",
    "image/pjpeg": "image/jpeg",
    "video/mov": "video/quicktime",
}
LABELS = {"image": "图片", "video": "视频"}
COLUMNS = "id,creation_id,draft_id,kind,filename,mime,bytes,sha256,created"


def normalize_mime(value):
    base = (value or "").split(";")[0].strip().lower()
    return MIME_ALIASES.get(base, base)


def clean_filename(value):
    name = os.path.basename((value or "").strip())
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "", name).strip()
    return name[:200]


class Media:
    def __init__(self, store):
        self.store = store
        self.root = Path(store.root) / "media"

    def folder(self, creation_id):
        path = self.root / creation_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def part(self, creation_id, media_id):
        return self.folder(creation_id) / (media_id + ".part")

    def sidecar(self, creation_id, media_id):
        return self.folder(creation_id) / (media_id + ".json")

    def meta(self, creation_id, media_id):
        if not IDENT.fullmatch(media_id):
            raise HTTPException(404, "附件不存在。")
        path = self.sidecar(creation_id, media_id)
        if not path.exists():
            raise HTTPException(404, "上传已结束或已过期，请重新选择文件。")
        try:
            return json.loads(path.read_text())
        except ValueError:
            raise HTTPException(404, "上传已结束或已过期，请重新选择文件。")

    def sweep(self, creation_id, now=None):
        """Drop abandoned staging files; an interrupted upload is not an error."""
        cutoff = (now or time.time()) - STALE_AFTER
        folder = self.root / creation_id
        if not folder.exists():
            return
        for path in folder.iterdir():
            if path.suffix not in (".part", ".json") or not IDENT.fullmatch(path.stem):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _staged(self, creation_id):
        folder = self.root / creation_id
        if not folder.exists():
            return []
        staged = []
        for path in folder.glob("*.json"):
            if not IDENT.fullmatch(path.stem):
                continue
            try:
                staged.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return staged

    def _check_limits(self, creation_id, kind, size):
        if size < 1:
            raise HTTPException(422, "这个文件是空的，请重新选择。")
        if size > MAX_BYTES:
            raise HTTPException(
                422, f"单个附件请控制在 {MAX_BYTES // (1024 * 1024)} MB 以内。"
            )
        rows = self.store.rows(
            "SELECT kind FROM creation_media WHERE creation_id=?", (creation_id,)
        )
        kinds = [row["kind"] for row in rows] + [
            item.get("kind") for item in self._staged(creation_id)
        ]
        if len(kinds) >= MAX_FILES:
            raise HTTPException(422, f"每条创作最多 {MAX_FILES} 个附件，请先删除一个。")
        if kind == "video" and kinds.count("video") >= MAX_VIDEOS:
            raise HTTPException(422, "一条创作只放一个视频，请先删除现有视频。")

    def start(self, creation_id, filename, mime, size, digest):
        mime = normalize_mime(mime)
        if mime not in MIME_KINDS:
            raise HTTPException(422, "只支持 PNG、JPEG、WebP、GIF 图片和 MP4、MOV、WebM 视频。")
        kind, extension = MIME_KINDS[mime]
        now = time.time()
        # Clear expired staging first: an abandoned upload holds a slot (it is
        # real bytes on disk), but it must not hold one forever.
        self.sweep(creation_id, now)
        self._check_limits(creation_id, kind, size)
        digest = (digest or "").strip().lower()
        if digest and not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise HTTPException(422, "校验值格式不正确，请重新选择文件。")
        media_id = uuid.uuid4().hex
        meta = {
            "id": media_id,
            "creation_id": creation_id,
            "kind": kind,
            "filename": clean_filename(filename) or f"附件{extension}",
            "mime": mime,
            "bytes": int(size),
            "sha256": digest,
            "extension": extension,
            "created": int(now),
        }
        self.sidecar(creation_id, media_id).write_text(
            json.dumps(meta, ensure_ascii=False)
        )
        self.sidecar(creation_id, media_id).chmod(0o600)
        self.part(creation_id, media_id).write_bytes(b"")
        self.part(creation_id, media_id).chmod(0o600)
        return {
            "id": media_id,
            "kind": kind,
            "filename": meta["filename"],
            "mime": mime,
            "bytes": meta["bytes"],
            "chunk_size": CHUNK,
            "chunks": max(1, ceil(meta["bytes"] / CHUNK)),
        }

    def append(self, creation_id, media_id, index, data):
        meta = self.meta(creation_id, media_id)
        path = self.part(creation_id, media_id)
        written = path.stat().st_size
        expected = written // CHUNK
        if index != expected:
            raise HTTPException(409, "附件分片顺序不对，请重新上传。")
        try:
            block = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "附件分片无法解析，请重新上传。")
        if not block:
            raise HTTPException(422, "附件分片为空，请重新上传。")
        if written + len(block) > meta["bytes"]:
            raise HTTPException(422, "附件比声明的更大，请重新上传。")
        with open(path, "ab") as handle:
            handle.write(block)
        received = written + len(block)
        return {"id": media_id, "received": received, "bytes": meta["bytes"]}

    def complete(self, creation_id, media_id):
        meta = self.meta(creation_id, media_id)
        part = self.part(creation_id, media_id)
        size = part.stat().st_size
        if size != meta["bytes"]:
            self._discard(creation_id, media_id)
            raise HTTPException(
                422, f"附件只收到 {size} 字节，需要 {meta['bytes']} 字节，请重新上传。"
            )
        hasher = hashlib.sha256()
        with open(part, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        digest = hasher.hexdigest()
        if meta["sha256"] and meta["sha256"] != digest:
            self._discard(creation_id, media_id)
            raise HTTPException(422, "附件校验失败，内容可能不完整，请重新上传。")
        target = self.folder(creation_id) / (media_id + meta["extension"])
        os.replace(part, target)
        target.chmod(0o600)
        relative = str(target.relative_to(self.store.root))
        with self.store.db(True) as c:
            draft = c.execute(
                "SELECT draft_id FROM creations WHERE id=?", (creation_id,)
            ).fetchone()
            c.execute(
                "INSERT INTO creation_media(id,creation_id,draft_id,kind,filename,mime,bytes,sha256,path,created)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    media_id,
                    creation_id,
                    draft["draft_id"] if draft else None,
                    meta["kind"],
                    meta["filename"],
                    meta["mime"],
                    size,
                    digest,
                    relative,
                    meta["created"],
                ),
            )
        self.sidecar(creation_id, media_id).unlink(missing_ok=True)
        return self.get(creation_id, media_id)

    def _discard(self, creation_id, media_id):
        self.part(creation_id, media_id).unlink(missing_ok=True)
        self.sidecar(creation_id, media_id).unlink(missing_ok=True)

    def rows(self, creation_id):
        return self.store.rows(
            f"SELECT {COLUMNS} FROM creation_media WHERE creation_id=? ORDER BY created, rowid",
            (creation_id,),
        )

    def _row(self, creation_id, media_id):
        rows = self.store.rows(
            "SELECT * FROM creation_media WHERE creation_id=? AND id=?",
            (creation_id, media_id),
        )
        if not rows:
            raise HTTPException(404, "附件不存在。")
        return rows[0]

    def get(self, creation_id, media_id):
        row = self._row(creation_id, media_id)
        return {key: row[key] for key in COLUMNS.split(",")}

    def read(self, creation_id, media_id, index):
        row = self._row(creation_id, media_id)
        chunks = max(1, ceil(row["bytes"] / CHUNK))
        if index < 0 or index >= chunks:
            raise HTTPException(404, "附件分片不存在。")
        path = self.store.root / row["path"]
        if not path.exists():
            raise HTTPException(404, "附件文件已不在本机，请重新上传。")
        with open(path, "rb") as handle:
            handle.seek(index * CHUNK)
            block = handle.read(CHUNK)
        return {
            "id": media_id,
            "index": index,
            "chunks": chunks,
            "bytes": row["bytes"],
            "filename": row["filename"],
            "mime": row["mime"],
            "kind": row["kind"],
            "data": base64.b64encode(block).decode(),
        }

    def remove(self, creation_id, media_id):
        row = self._row(creation_id, media_id)
        path = self.store.root / row["path"]
        path.unlink(missing_ok=True)
        self._discard(creation_id, media_id)
        with self.store.db(True) as c:
            c.execute(
                "DELETE FROM creation_media WHERE creation_id=? AND id=?",
                (creation_id, media_id),
            )
        return {"removed": media_id}
