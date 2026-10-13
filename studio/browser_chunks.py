"""Bounded reassembly of one authenticated browser result."""

import base64
import binascii
import hashlib
import json
import re


MAX_CHUNK_BYTES = 98_304
MAX_RESULT_BYTES = 8_500_000
MAX_CHUNKS = (MAX_RESULT_BYTES + MAX_CHUNK_BYTES - 1) // MAX_CHUNK_BYTES


class ChunkError(ValueError):
    pass


class ResultChunks:
    def __init__(self, action_id):
        self.action_id = str(action_id)
        self.total = None
        self.digest = None
        self.parts = []
        self.size = 0

    def accept(self, frame):
        if not isinstance(frame, dict) or frame.get("type") != "result_chunk":
            raise ChunkError("chunk_frame_invalid")
        if str(frame.get("id")) != self.action_id:
            raise ChunkError("chunk_action_mismatch")
        total = frame.get("total")
        index = frame.get("index")
        digest = frame.get("sha256")
        if (type(total) is not int or not 1 <= total <= MAX_CHUNKS
                or type(index) is not int or index != len(self.parts)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ChunkError("chunk_sequence_invalid")
        if self.total is None:
            self.total, self.digest = total, digest
        elif total != self.total or digest != self.digest:
            raise ChunkError("chunk_envelope_mismatch")
        body = frame.get("body")
        if not isinstance(body, str) or len(body) > 131_072:
            raise ChunkError("chunk_size_invalid")
        try:
            part = base64.b64decode(body, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ChunkError("chunk_encoding_invalid") from exc
        if not part or len(part) > MAX_CHUNK_BYTES:
            raise ChunkError("chunk_size_invalid")
        self.size += len(part)
        if self.size > MAX_RESULT_BYTES:
            raise ChunkError("result_too_large")
        self.parts.append(part)
        if len(self.parts) != self.total:
            return None
        raw = b"".join(self.parts)
        if hashlib.sha256(raw).hexdigest() != self.digest:
            raise ChunkError("chunk_digest_mismatch")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ChunkError("result_json_invalid") from exc
        if not isinstance(result, dict) or result.get("type") != "result" or str(result.get("id")) != self.action_id:
            raise ChunkError("result_action_mismatch")
        return result
