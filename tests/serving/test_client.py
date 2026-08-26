import unittest

from src.serving.client import OpenAICompatibleClient
from src.serving.schemas import ServingEndpoint, ServingError, ServingTask


class FakeTransport:
    """Routes POSTs by path to canned responses; records calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def post_json(self, url, payload, *, timeout_s):
        self.calls.append((url, dict(payload), timeout_s))
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected url {url}")


def client(task, responses):
    endpoint = ServingEndpoint(
        name="svc", base_url="http://localhost:9000", model_id="m", task=task, timeout_s=5.0
    )
    return OpenAICompatibleClient(endpoint, FakeTransport(responses))


class GenerateTests(unittest.TestCase):
    def test_generate_returns_message_content(self):
        cli = client(
            ServingTask.GENERATE,
            {"/v1/chat/completions": {"choices": [{"message": {"content": "hello"}}]}},
        )
        self.assertEqual(cli.generate([{"role": "user", "content": "hi"}]), "hello")
        self.assertEqual(cli.transport.calls[0][1]["model"], "m")

    def test_generate_rejects_wrong_task(self):
        cli = client(ServingTask.EMBED, {})
        with self.assertRaises(ServingError) as ctx:
            cli.generate([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "SERVING_TASK_MISMATCH")


class EmbedTests(unittest.TestCase):
    def test_embed_orders_by_index(self):
        cli = client(
            ServingTask.EMBED,
            {
                "/v1/embeddings": {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                }
            },
        )
        self.assertEqual(cli.embed(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])

    def test_embed_count_mismatch_is_typed(self):
        cli = client(ServingTask.EMBED, {"/v1/embeddings": {"data": [{"index": 0, "embedding": [1.0]}]}})
        with self.assertRaises(ServingError) as ctx:
            cli.embed(["a", "b"])
        self.assertEqual(ctx.exception.code, "SERVING_PROTOCOL_INVALID")


class ScoreTests(unittest.TestCase):
    def test_score_orders_by_index(self):
        cli = client(
            ServingTask.SCORE,
            {"/score": {"data": [{"index": 1, "score": 0.2}, {"index": 0, "score": 0.9}]}},
        )
        self.assertEqual(cli.score("q", ["d0", "d1"]), [0.9, 0.2])

    def test_non_numeric_score_is_typed(self):
        cli = client(ServingTask.SCORE, {"/score": {"data": [{"index": 0, "score": "x"}]}})
        with self.assertRaises(ServingError):
            cli.score("q", ["d0"])


class TokenizeTests(unittest.TestCase):
    def test_count_tokens_and_pairs(self):
        cli = client(ServingTask.EMBED, {"/tokenize": {"count": 7}})
        self.assertEqual(cli.count_tokens("abc"), 7)
        pair = client(ServingTask.SCORE, {"/tokenize": {"count": 11}})
        self.assertEqual(pair.count_pair_tokens("q", "d"), 11)

    def test_missing_count_is_typed(self):
        cli = client(ServingTask.EMBED, {"/tokenize": {}})
        with self.assertRaises(ServingError):
            cli.count_tokens("abc")


class TransportFailureTests(unittest.TestCase):
    def test_transport_exception_is_wrapped(self):
        cli = client(ServingTask.EMBED, {"/v1/embeddings": RuntimeError("boom")})
        with self.assertRaises(ServingError) as ctx:
            cli.embed(["a"])
        self.assertEqual(ctx.exception.code, "SERVING_TRANSPORT_FAILED")

    def test_serving_error_from_transport_is_not_rewrapped(self):
        cli = client(ServingTask.EMBED, {"/v1/embeddings": ServingError("SERVING_PROTOCOL_INVALID", "x")})
        with self.assertRaises(ServingError) as ctx:
            cli.embed(["a"])
        self.assertEqual(ctx.exception.code, "SERVING_PROTOCOL_INVALID")


if __name__ == "__main__":
    unittest.main()
