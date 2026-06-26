#!/usr/bin/env python3
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thoughts_tokens",
    "tool_use_prompt_tokens",
    "total_tokens",
)
RETRYABLE_VERTEX_CODES = {429, 500, 502, 503, 504}
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_CALLS = 64
DEFAULT_MAX_PROMPT_CHARS = 200_000
DEFAULT_MAX_REQUEST_BYTES = 300_000


def build_client():
    try:
        from google import genai
    except ModuleNotFoundError:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt")

    api_key = os.environ.get("EXPRESS_MODE_KEY")
    if not api_key:
        raise RuntimeError("Environment variable 'EXPRESS_MODE_KEY' not found.")

    return genai.Client(
        vertexai=True,
        api_key=api_key,
        http_options={"headers": {"X-Goog-User-Project": ""}},
    )


def make_generate_config(max_output_tokens, thinking_budget):
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
    )


def extract_response_text(response):
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if text:
        return text

    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(part_text)
    return "".join(chunks)


def enum_text(value):
    return getattr(value, "name", str(value)) if value is not None else None


def int_value(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def raw_metadata(usage):
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)
    if hasattr(usage, "dict"):
        return usage.dict(exclude_none=True)
    return {}


def usage_tokens(response):
    usage = getattr(response, "usage_metadata", None)
    return {
        "input_tokens": int_value(getattr(usage, "prompt_token_count", 0)),
        "output_tokens": int_value(getattr(usage, "candidates_token_count", 0)),
        "cache_read_tokens": int_value(getattr(usage, "cached_content_token_count", 0)),
        "cache_write_tokens": 0,
        "thoughts_tokens": int_value(getattr(usage, "thoughts_token_count", 0)),
        "tool_use_prompt_tokens": int_value(getattr(usage, "tool_use_prompt_token_count", 0)),
        "total_tokens": int_value(getattr(usage, "total_token_count", 0)),
        "usage_source": "provider",
        "raw_usage_metadata": raw_metadata(usage),
    }


def estimate_token_count(text):
    if not text:
        return 0
    # Portable fallback for development upstreams that do not report usage.
    return max(1, (len(text) + 3) // 4)


def normalize_usage(value, prompt, text, default_source):
    data = value if isinstance(value, dict) else {}
    normalized = {key: int_value(data.get(key, 0)) for key in TOKEN_KEYS}
    source = data.get("usage_source") or default_source

    if not any(normalized.values()):
        normalized["input_tokens"] = estimate_token_count(prompt)
        normalized["output_tokens"] = estimate_token_count(text)
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
        source = "estimated"
    elif not normalized["total_tokens"]:
        normalized["total_tokens"] = (
            normalized["input_tokens"]
            + normalized["output_tokens"]
            + normalized["cache_read_tokens"]
            + normalized["cache_write_tokens"]
            + normalized["thoughts_tokens"]
            + normalized["tool_use_prompt_tokens"]
        )

    raw = data.get("raw_usage_metadata", {})
    normalized["usage_source"] = source
    normalized["raw_usage_metadata"] = raw if isinstance(raw, dict) else {}
    return normalized


def public_usage(token_values):
    usage = {key: int_value(token_values.get(key, 0)) for key in TOKEN_KEYS}
    usage["usage_source"] = token_values.get("usage_source", "unknown")
    return usage


def error_code(exc):
    for name in ("code", "status_code"):
        value = getattr(exc, name, None)
        if value is not None:
            return int_value(value)
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return int_value(value) if value is not None else None


def is_retryable_vertex_error(exc):
    code = error_code(exc)
    if code in RETRYABLE_VERTEX_CODES:
        return True
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()


def vertex_error_response(exc):
    provider_status = error_code(exc)
    retryable = is_retryable_vertex_error(exc)
    if provider_status and 400 <= provider_status <= 599:
        http_status = provider_status
    else:
        http_status = 503 if retryable else 500

    return http_status, {
        "error": str(exc),
        "retryable": retryable,
        "provider": "vertexai",
        "provider_status": provider_status,
    }


def response_diagnostics(response, text):
    diagnostics = {
        "prompt_feedback": enum_text(getattr(getattr(response, "prompt_feedback", None), "block_reason", None)),
        "text_chars": len(text or ""),
        "candidates": [],
    }
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        diagnostics["candidates"].append({
            "finish_reason": enum_text(getattr(candidate, "finish_reason", None)),
            "part_count": len(getattr(content, "parts", []) or []),
        })
    return diagnostics


def generate_url(endpoint):
    base = endpoint.rstrip("/")
    if base.endswith("/generate"):
        return base
    return base + "/generate"


def read_error_payload(exc):
    text = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"error": text}
    return payload if isinstance(payload, dict) else {"error": text}


def call_vertex(client, args, model, prompt, max_output_tokens):
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=make_generate_config(max_output_tokens, args.thinking_budget),
    )
    text = extract_response_text(response)
    diagnostics = response_diagnostics(response, text)
    token_values = usage_tokens(response)
    return {
        "text": text or "",
        "diagnostics": diagnostics,
        "usage": token_values,
    }


def call_upstream(args, request, model, prompt, max_output_tokens):
    payload = dict(request)
    payload["model"] = model
    payload["prompt"] = prompt
    payload["max_output_tokens"] = max_output_tokens
    body = json.dumps(payload).encode("utf-8")
    http_request = urllib.request.Request(
        generate_url(args.upstream_endpoint),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(http_request, timeout=args.upstream_timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(response_payload, dict):
        raise RuntimeError("Upstream endpoint returned a non-object JSON response.")
    text = response_payload.get("text") or ""
    if not isinstance(text, str):
        text = str(text)
    diagnostics = response_payload.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        diagnostics = {"upstream_diagnostics": diagnostics}
    diagnostics.setdefault("upstream_endpoint", args.upstream_endpoint)

    token_values = normalize_usage(response_payload.get("usage"), prompt, text, "upstream")
    return {
        "text": text,
        "diagnostics": diagnostics,
        "usage": token_values,
    }


def initial_usage(args):
    return {
        "schema_version": "1",
        "provider": args.provider,
        "run_id": args.run_id,
        "case_name": args.case_name,
        "design_type": args.design_type,
        "task_type": args.task_type,
        "model": args.model,
        "calls": [],
        "totals": {key: 0 for key in TOKEN_KEYS},
    }


def append_usage(args, call):
    usage_path = Path(args.usage_path)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    if usage_path.is_file():
        with usage_path.open(encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = initial_usage(args)

    data.setdefault("calls", []).append(call)
    totals = {key: 0 for key in TOKEN_KEYS}
    for entry in data["calls"]:
        for key in TOKEN_KEYS:
            totals[key] += int_value(entry.get(key, 0))
    totals["num_calls"] = len(data["calls"])
    data["totals"] = totals
    usage_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class ModelHandler(BaseHTTPRequestHandler):
    server_version = "ICLADModelService/1"

    def log_message(self, fmt, *args):
        return

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > self.server.args.max_request_bytes:
            raise ValueError(
                f"Request body is too large: {length} bytes > {self.server.args.max_request_bytes} byte limit."
            )
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.write_json(200, {"status": "ok"})
        else:
            self.write_json(404, {"error": "not_found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/generate":
            self.write_json(404, {"error": "not_found"})
            return

        try:
            request = self.read_json()
            prompt = request.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise ValueError("Request field 'prompt' must be a non-empty string.")
            if len(prompt) > self.server.args.max_prompt_chars:
                raise ValueError(
                    f"Request field 'prompt' is too large: {len(prompt)} chars "
                    f"> {self.server.args.max_prompt_chars} char limit."
                )

            model = request.get("model") or self.server.args.model
            max_output_tokens = int(request.get("max_output_tokens") or self.server.args.max_output_tokens)
        except (json.JSONDecodeError, ValueError) as exc:
            self.write_json(400, {"error": str(exc), "retryable": False})
            return
        except Exception as exc:
            self.write_json(500, {"error": str(exc), "retryable": False})
            return

        with self.server.usage_lock:
            if self.server.call_count >= self.server.args.max_calls:
                self.write_json(429, {
                    "error": f"Model call limit exceeded: {self.server.args.max_calls} calls per case.",
                    "retryable": False,
                    "provider": self.server.args.provider,
                    "provider_status": None,
                })
                return
            self.server.call_count += 1
            call_id = f"{self.server.args.case_name}_{self.server.call_count:04d}"

        print(f"[INFO] Model request {call_id} using {model}", file=sys.stderr, flush=True)
        try:
            if self.server.args.upstream_endpoint:
                result = call_upstream(self.server.args, request, model, prompt, max_output_tokens)
            else:
                result = call_vertex(self.server.client, self.server.args, model, prompt, max_output_tokens)
        except urllib.error.HTTPError as exc:
            payload = read_error_payload(exc)
            payload.setdefault("retryable", exc.code in RETRYABLE_HTTP_CODES)
            payload.setdefault("provider", self.server.args.provider)
            payload.setdefault("provider_status", exc.code)
            self.write_json(exc.code, payload)
            return
        except urllib.error.URLError as exc:
            self.write_json(503, {
                "error": str(exc),
                "retryable": True,
                "provider": self.server.args.provider,
                "provider_status": None,
            })
            return
        except Exception as exc:
            if self.server.args.upstream_endpoint:
                status, payload = 500, {
                    "error": str(exc),
                    "retryable": False,
                    "provider": self.server.args.provider,
                    "provider_status": None,
                }
            else:
                status, payload = vertex_error_response(exc)
            self.write_json(status, payload)
            return

        text = result["text"]
        diagnostics = result["diagnostics"]
        token_values = result["usage"]

        with self.server.usage_lock:
            append_usage(self.server.args, {
                "call_id": call_id,
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **{key: token_values[key] for key in TOKEN_KEYS},
                "usage_source": token_values.get("usage_source", "unknown"),
                "raw_usage_metadata": token_values.get("raw_usage_metadata", {}),
                "diagnostics": diagnostics,
            })

        self.write_json(200, {
            "text": text or "",
            "diagnostics": diagnostics,
            "usage": public_usage(token_values),
        })


def main():
    parser = argparse.ArgumentParser(description="ICLAD benchmark model service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="vertexai")
    parser.add_argument("--upstream-endpoint", default="")
    parser.add_argument("--upstream-timeout", type=int, default=300)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case-name", required=True)
    parser.add_argument("--design-type", default="block")
    parser.add_argument("--task-type", default="repair")
    parser.add_argument("--usage-path", required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--max-prompt-chars", type=int, default=DEFAULT_MAX_PROMPT_CHARS)
    parser.add_argument("--max-request-bytes", type=int, default=DEFAULT_MAX_REQUEST_BYTES)
    parser.add_argument("--thinking-budget", type=int, default=0)
    args = parser.parse_args()
    if args.upstream_endpoint and args.provider == "vertexai":
        args.provider = "upstream"

    client = None
    if not args.upstream_endpoint:
        try:
            client = build_client()
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
            sys.exit(1)
    server = ThreadingHTTPServer((args.host, args.port), ModelHandler)
    server.args = args
    server.client = client
    server.call_count = 0
    server.usage_lock = threading.Lock()
    print(f"[INFO] Model service listening on http://{args.host}:{args.port}", flush=True)
    if args.upstream_endpoint:
        print(f"[INFO] Forwarding model requests to upstream {args.upstream_endpoint}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
