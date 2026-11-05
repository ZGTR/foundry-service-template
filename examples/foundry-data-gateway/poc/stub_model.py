"""A deterministic, OpenAI-compatible streaming Chat Completions stub. NOT a model.

It lets the end-to-end run without a provider key or spend. It plays a model's two moves:

* the last message is the user's: pick ONE data tool by keyword and stream its call
  (`struggl`/`help` -> struggling_learners, `progress` + a subject slug -> subject_progress,
  `subject` -> list_subjects); otherwise answer in text.
* the last message is a tool result: read the fenced JSON foundry-agent hands back and
  stream a short answer from it, or say why the gateway refused.

Every request is appended to `--log` (one JSON line), so the proof shows exactly what the
runtime sent the "model", including the untrusted-data fence around the rows.

Swap it for a real model by pointing foundry-agent's MODEL_API_BASE_URL elsewhere.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SLUGS = ("arabic-letters", "counting", "animals", "arabic-words", "addition", "reading")


def choose_tool(text: str) -> tuple[str, dict] | None:
    lowered = text.lower()
    if "struggl" in lowered or "help" in lowered:
        below = re.search(r"below (\d+)", lowered)
        return "struggling_learners", {"below_score": int(below.group(1)) if below else 60}
    if "progress" in lowered:
        slug = next((s for s in SLUGS if s in lowered), "arabic-letters")
        return "subject_progress", {"subject_slug": slug}
    if "subject" in lowered:
        age = re.search(r"\b(3-4|5-6|7-8)\b", lowered)
        return "list_subjects", {"age_group": age.group(1)} if age else {}
    if "email" in lowered:
        return "learner_emails", {}  # not in the catalogue: the runtime refuses it unsent
    return None


def unfence(content: str) -> dict:
    outer = json.loads(content)
    if not outer.get("ok"):
        return {"ok": False, "reason": outer.get("error", "refused")}
    inner = outer.get("result")
    if isinstance(inner, str) and inner.startswith("<untrusted_data"):
        inner = json.loads(inner.split("\n", 1)[1].rsplit("\n", 1)[0])
    return inner if isinstance(inner, dict) else {"ok": True}


def answer_from(result: dict) -> str:
    if not result.get("ok"):
        return f"I can't answer that from the school's data: {result.get('reason')} (rule: {result.get('refused', 'n/a')})."
    rows = result.get("rows", [])
    if not rows:
        return "No matching records."
    tool = result.get("tool")
    if tool == "struggling_learners":
        items = "; ".join(f"{r['learner']} in {r['subject']} (avg {r['avg_score']})" for r in rows)
        return f"{len(rows)} learner-subject pairs need help, weakest first: {items}."
    if tool == "subject_progress":
        items = "; ".join(
            f"{r['learner']}: {r['bytes_completed']} bytes done, avg {r['avg_score']}" for r in rows
        )
        return f"Progress for {len(rows)} learners: {items}."
    items = "; ".join(
        f"{r['title_en']} / {r['title_ar']} ({r['age_group']}, {r['bytes']} bytes)" for r in rows
    )
    return f"{len(rows)} subjects: {items}."


class Handler(BaseHTTPRequestHandler):
    log_path: Path

    def log_message(self, *_args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(body) + "\n")
        last = (body.get("messages") or [{}])[-1]
        base = {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "model": body["model"]}
        chunks: list[dict] = []
        pick = choose_tool(str(last.get("content") or "")) if last.get("role") == "user" else None
        if pick and body.get("tools"):
            name, args = pick
            call = {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            chunks.append(
                {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}}]}
            )
            finish = "tool_calls"
        else:
            text = (
                answer_from(unfence(last["content"]))
                if last.get("role") == "tool"
                else ("Ask me about subjects, a subject's progress, or learners who need help.")
            )
            for i, word in enumerate(text.split(" ")):
                chunks.append(
                    {
                        "choices": [
                            {"index": 0, "delta": {"content": word if i == 0 else " " + word}}
                        ]
                    }
                )
            finish = "stop"
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        chunks.append({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40}})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps({**base, **chunk})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18190)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    Handler.log_path = args.log
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
