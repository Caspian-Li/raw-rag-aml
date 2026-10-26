import asyncio
from datetime import datetime, timezone
import hashlib
import json

from .schemas import AddRequest
from .storage import Store
from .upstream import CompetitionAdapter, Conversation, Message, add_vector_cache


class Engine:
    def __init__(self, settings, adapter=None):
        self.settings = settings
        self.store = Store(settings.database, settings.fingerprint())
        self.adapter = adapter
        self.locks = {}
        self.add_slots = asyncio.Semaphore(settings.add_concurrency)
        self.search_slots = asyncio.Semaphore(settings.search_concurrency)

    async def start(self):
        try:
            await asyncio.to_thread(self.store.initialize)
            if self.adapter is None:
                self.adapter = CompetitionAdapter(self.settings)
            await asyncio.to_thread(self.store.expire, self.settings.retention_days)
        except BaseException:
            self.store.close()
            raise

    async def close(self):
        try:
            if self.adapter:
                await self.adapter.close()
        finally:
            self.store.close()

    def user_lock(self, user_id):
        return self.locks.setdefault(user_id, asyncio.Lock())

    async def add(self, payload: AddRequest):
        digest = hashlib.sha256(json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        async with self.user_lock(payload.user_id):
            prior = await asyncio.to_thread(self.store.receipt, payload.request_id, digest)
            if prior:
                return prior
            async with self.add_slots:
                snapshot = await asyncio.to_thread(self.store.snapshot, payload.user_id, True)
                records = snapshot["messages"] + [dict(m.model_dump(), session_id=payload.session_id) for m in payload.messages]
                messages = []
                for record in records:
                    timestamp = record.get("timestamp")
                    date = datetime.fromtimestamp(timestamp / 1000, timezone.utc) if timestamp is not None else None
                    messages.append(Message(
                        sender_id=record["role"], sender_name=record["role"], content=record["content"],
                        timestamp=date, metadata={"session": record["session_id"],
                                                  "session_date": date.date().isoformat() if date else None},
                    ))
                # Never use benchmark-prefixed IDs: no dataset-specific shortcuts.
                conversation_id = "aml_" + hashlib.sha256(payload.user_id.encode()).hexdigest()
                conversation = Conversation(conversation_id, messages, {"speaker_a": "user", "speaker_b": "assistant"})
                previous = snapshot["index"]
                cache = {} if previous is None else {
                    hashlib.sha256(chunk["content"].encode()).hexdigest(): vector
                    for chunk, vector in zip(previous["chunks"], previous["embeddings"])
                }
                token = add_vector_cache.set(cache)
                try:
                    built = await self.adapter._build_conversation_index(conversation)
                finally:
                    add_vector_cache.reset(token)
                if not built.get("chunks") or len(built["chunks"]) != len(built.get("embeddings", [])):
                    raise RuntimeError("Incomplete index cannot be committed")
                return await asyncio.to_thread(self.store.commit_add, payload, digest, snapshot["revision"], built)

    async def search(self, payload):
        async with self.user_lock(payload.user_id):
            async with self.search_slots:
                snapshot = await asyncio.to_thread(self.store.snapshot, payload.user_id)
                conversation = snapshot["index"]
                if conversation is None:
                    return {"data": []}
                index = {"conversations": {conversation["conversation_id"]: conversation}}
                result = await self.adapter.search(payload.query, conversation["conversation_id"], index)
                if result.retrieval_metadata.get("error"):
                    raise RuntimeError("Retrieval failed")
                data = []
                for item in result.results[:payload.top_k]:
                    chunk = dict(item["metadata"], content=item["content"])
                    content = self.adapter._expand_chunk_to_turns(chunk, conversation["turn_context"])
                    if not content.strip():
                        raise RuntimeError("Empty evidence cannot be returned")
                    identity = hashlib.sha256((payload.user_id + "\0" + str(chunk["start_token"]) + "\0" + content).encode()).hexdigest()
                    data.append({"id": "mem_" + identity, "content": content,
                                 "score": item["score"], "created_at": snapshot["created_at"][chunk["chunk_index"]]})
                return {"data": data}
