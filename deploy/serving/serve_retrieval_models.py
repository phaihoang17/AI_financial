"""Minimal OpenAI-compatible HTTP server for the pinned retrieval models.

ADR-057 runbook step H/I says "example vLLM; any OpenAI-compatible server
works". This is that server, kept deliberately thin: it performs **no** model
logic of its own. It loads the repository's pinned contracts

  * ``src.indexing.embedding_indexer.load_bge_m3_encoder``  (BGE-M3, EMBED)
  * ``src.retrieval.reranker.load_bge_reranker``            (BGE-reranker-v2-m3, SCORE)

on CUDA and exposes exactly the three routes
``src.serving.client.OpenAICompatibleClient`` calls, plus ``/v1/models`` for
health checks:

    POST /v1/embeddings   {"model", "input": [str, ...]}
                          -> {"data": [{"index", "embedding": [float x1024]}, ...]}
    POST /score           {"model", "text_1": str, "text_2": [str, ...]}
                          -> {"data": [{"index", "score": float}, ...]}
    POST /tokenize        {"prompt": str} | {"text_1": str, "text_2": str}
                          -> {"count": int}
    GET  /v1/models       -> {"data": [{"id": <model_id>}]}

Vectors are the encoder's own mean-pooled + L2-normalized output (the same
values ``load_bge_m3_encoder(...).encode`` returns on CPU for the parity
reference); scores are the cross-encoder's raw ``logits[:, 0]``.

Usage:
    CUDA_VISIBLE_DEVICES=0 python -m deploy.serving.serve_retrieval_models \
        --embed-port 18100 --rerank-port 18101 --host 127.0.0.1

The two models share one process here for single-GPU convenience; the client
still sees two independent base URLs (one per port), which is all
``ServingTopology`` failure-isolation and the two probes require.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict

from src.indexing.embedding_chunker import BGE_M3_MODEL_ID
from src.indexing.embedding_indexer import load_bge_m3_encoder
from src.retrieval.reranker import BGE_RERANKER_MODEL_ID, load_bge_reranker

# BGE-M3 was trained with an 8192-token context; the reranker pair guard lives
# in src.retrieval.reranker (RERANKER_MAX_PAIR_TOKENS) and is not re-applied here.
_EMBED_MAX_LENGTH = 8192
_INFERENCE_LOCK = threading.Lock()


class _Models:
    """Lazily-loaded singletons for the two pinned encoders."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.encoder = load_bge_m3_encoder(device=device)
        self.reranker = load_bge_reranker(device=device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        with _INFERENCE_LOCK:
            return self.encoder.encode(
                texts, batch_size=max(1, len(texts)), max_length=_EMBED_MAX_LENGTH
            )

    def score(self, query: str, documents: list[str]) -> list[float]:
        with _INFERENCE_LOCK:
            return self.reranker.score_pairs(
                query, documents, batch_size=max(1, len(documents))
            )

    def count_prompt_tokens(self, text: str) -> int:
        with _INFERENCE_LOCK:
            return int(self.encoder.count_tokens([text], max_length=_EMBED_MAX_LENGTH)[0])

    def count_pair_tokens(self, text_1: str, text_2: str) -> int:
        with _INFERENCE_LOCK:
            enc = self.reranker.tokenizer(
                [text_1], [text_2], add_special_tokens=True, truncation=False,
                return_attention_mask=False,
            )
            return len(enc["input_ids"][0])


def _make_handler(models: _Models, served_model_id: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # keep stdout for real errors
            return

        def _send(self, code: int, body: Dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._send(200, {"object": "list", "data": [{"id": served_model_id, "object": "model"}]})
                return
            if self.path.rstrip("/") in ("", "/health", "/ping"):
                self._send(200, {"status": "ok", "model": served_model_id})
                return
            self._send(404, {"error": {"message": f"no route {self.path}"}})

        def do_POST(self) -> None:
            route = self.path.split("?", 1)[0].rstrip("/")
            handlers: Dict[str, Callable[[Any], Dict[str, Any]]] = {
                "/v1/embeddings": self._embeddings,
                "/embeddings": self._embeddings,
                "/score": self._score,
                "/v1/score": self._score,
                "/tokenize": self._tokenize,
            }
            fn = handlers.get(route)
            if fn is None:
                self._send(404, {"error": {"message": f"no route {self.path}"}})
                return
            try:
                body = self._read_json()
                self._send(200, fn(body))
            except (KeyError, TypeError, ValueError) as error:
                self._send(400, {"error": {"message": f"{type(error).__name__}: {error}"}})
            except Exception as error:  # noqa: BLE001 - surface model failures as 500
                self._send(500, {"error": {"message": f"{type(error).__name__}: {error}"}})

        # --- route bodies -------------------------------------------------- #
        def _embeddings(self, body: Any) -> Dict[str, Any]:
            raw = body["input"]
            texts = [raw] if isinstance(raw, str) else [str(t) for t in raw]
            if not texts:
                raise ValueError("input must be non-empty")
            vectors = models.embed(texts)
            return {
                "object": "list",
                "model": served_model_id,
                "data": [
                    {"object": "embedding", "index": i, "embedding": [float(x) for x in vec]}
                    for i, vec in enumerate(vectors)
                ],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }

        def _score(self, body: Any) -> Dict[str, Any]:
            query = body["text_1"]
            raw = body["text_2"]
            docs = [raw] if isinstance(raw, str) else [str(d) for d in raw]
            if not isinstance(query, str) or not docs:
                raise ValueError("text_1 must be a string and text_2 non-empty")
            scores = models.score(query, docs)
            return {
                "object": "list",
                "model": served_model_id,
                "data": [{"index": i, "score": float(s)} for i, s in enumerate(scores)],
            }

        def _tokenize(self, body: Any) -> Dict[str, Any]:
            if "prompt" in body:
                count = models.count_prompt_tokens(str(body["prompt"]))
            elif "text_1" in body and "text_2" in body:
                count = models.count_pair_tokens(str(body["text_1"]), str(body["text_2"]))
            else:
                raise KeyError("prompt or (text_1, text_2)")
            return {"count": int(count), "model": served_model_id}

    return Handler


def _serve(host: str, port: int, models: _Models, served_model_id: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _make_handler(models, served_model_id))
    thread = threading.Thread(target=server.serve_forever, name=f"serve:{port}", daemon=True)
    thread.start()
    print(f"[serve] {served_model_id} on http://{host}:{port}", flush=True)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--embed-port", type=int, required=True)
    parser.add_argument("--rerank-port", type=int, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args(argv)

    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA requested but torch.cuda.is_available() is False")
    print(f"[serve] loading pinned models on {args.device} ...", flush=True)
    models = _Models(args.device)
    if args.device == "cuda":
        print(f"[serve] cuda device: {torch.cuda.get_device_name(0)}", flush=True)

    servers = [
        _serve(args.host, args.embed_port, models, BGE_M3_MODEL_ID),
        _serve(args.host, args.rerank_port, models, BGE_RERANKER_MODEL_ID),
    ]
    print("[serve] ready", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
