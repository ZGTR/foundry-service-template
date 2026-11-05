"""A deterministic, OpenAI-compatible streaming Chat Completions server. NOT a model.

It lets the end-to-end suite run anywhere with no provider key and no spend, for ANY
tool pack. It plays a model's two moves using only what foundry-agent sends it:

* last message from the user: score each offered tool by word overlap between the
  question and the tool's name and description; stream a call to the best one with `{}`
  (every v1 tool has defaults). A question about e-mail asks for `contact_emails`, a tool
  no pack offers, to prove foundry-agent refuses an invented tool before any data call.
* last message a tool result: unwrap foundry-agent's untrusted-data fence and stream a
  short answer that names the rows, or the rule that refused the call.

Every request is appended to `--log` as one JSON line, so the proof shows what the model
was given. Replace it with a real model by pointing foundry-agent's MODEL_API_BASE_URL
at any OpenAI-compatible endpoint that streams tool calls.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WORD = re.compile(r"[a-z]{4,}")
_STOP = {
    "which",
    "what",
    "this",
    "that",
    "with",
    "from",
    "have",
    "were",
    "each",
    "they",
    "them",
    "show",
    "much",
    "many",
    "does",
    "tell",
    "about",
    "last",
    "days",
    "their",
    "there",
}


def words(text: str) -> set[str]:
    return {w.rstrip("s") for w in _WORD.findall(text.lower())} - _STOP


def choose_tool(question: str, tools: list[dict]) -> str | None:
    if "email" in question.lower():
        return "contact_emails"
    asked = words(question)
    best, best_score = None, 0
    for tool in tools:
        fn = tool["function"]
        score = 3 * len(asked & words(fn["name"].replace("_", " "))) + len(
            asked & words(fn.get("description", ""))
        )
        if score > best_score:
            best, best_score = fn["name"], score
    return best


def unfence(content: str) -> dict:
    outer = json.loads(content)
    if not outer.get("ok"):
        return {"ok": False, "reason": outer.get("error", "refused"), "refused": "runtime"}
    inner = outer.get("result")
    if isinstance(inner, str) and inner.startswith("<untrusted_data"):
        inner = json.loads(inner.split("\n", 1)[1].rsplit("\n", 1)[0])
    return inner if isinstance(inner, dict) else {"ok": True, "rows": []}


def answer(result: dict) -> str:
    if not result.get("ok"):
        return f"I can't answer that: {result.get('reason')} (rule: {result.get('refused')})."
    rows = result.get("rows") or []
    if not rows:
        return "No matching records."
    columns = result.get("columns") or list(rows[0])
    parts = []
    for row in rows[:6]:
        values = [(c, row.get(c)) for c in columns]
        head = f"{values[0][1]}"
        rest = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in values[1:4])
        parts.append(f"{head} ({rest})")
    more = f" and {len(rows) - 6} more" if len(rows) > 6 else ""
    return f"{len(rows)} results from {result.get('tool')}: " + "; ".join(parts) + more + "."


class Handler(BaseHTTPRequestHandler):
    log_path: Path

    def log_message(self, *_args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(body, ensure_ascii=False) + "\n")
        last = (body.get("messages") or [{}])[-1]
        base = {
            "id": "chatcmpl-deterministic",
            "object": "chat.completion.chunk",
            "model": body["model"],
        }
        chunks: list[dict] = []
        name = (
            choose_tool(str(last.get("content") or ""), body.get("tools") or [])
            if last.get("role") == "user"
            else None
        )
        if name:
            call = {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            chunks.append(
                {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}}]}
            )
            finish = "tool_calls"
        else:
            text = (
                answer(unfence(last["content"]))
                if last.get("role") == "tool"
                else ("I can answer questions about the data your organisation has granted me.")
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
        chunks.append({"choices": [], "usage": {"prompt_tokens": 180, "completion_tokens": 60}})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(
                f"data: {json.dumps({**base, **chunk}, ensure_ascii=False)}\n\n".encode()
            )
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
