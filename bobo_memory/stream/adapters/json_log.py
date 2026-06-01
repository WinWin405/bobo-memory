"""JSON log adapter — ingests structured JSON/JSONL event streams."""

from __future__ import annotations

import json
from typing import Any

from bobo_memory.stream.adapters.base import RawDoc, StreamAdapter


class JsonLogAdapter(StreamAdapter):
    """Parses JSON or JSONL payloads into RawDoc objects."""

    name = "json_log"

    def parse(self, payload: Any, **kwargs) -> list[RawDoc]:
        if isinstance(payload, str):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                lines = [l for l in payload.splitlines() if l.strip()]
                records = []
                for line in lines:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                data = records
        elif isinstance(payload, (dict, list)):
            data = payload
        else:
            data = str(payload)

        if isinstance(data, dict):
            data = [data]

        docs = []
        for record in (data if isinstance(data, list) else [data]):
            title = str(record.get("title") or record.get("name") or "json-event")
            body = json.dumps(record, indent=2, ensure_ascii=False)
            tags = record.get("tags") or []
            docs.append(RawDoc(title=title, body=body, tags=tags, adapter=self.name))

        return docs
