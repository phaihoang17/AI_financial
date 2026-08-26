import unittest

from src.serving.schemas import ServingEndpoint, ServingError, ServingTask


def endpoint(**overrides) -> ServingEndpoint:
    base = dict(
        name="qwen3-8b",
        base_url="http://localhost:8001",
        model_id="Qwen/Qwen3-8B",
        task=ServingTask.GENERATE,
        timeout_s=30.0,
    )
    base.update(overrides)
    return ServingEndpoint(**base)


class ServingEndpointTests(unittest.TestCase):
    def test_valid_endpoint_round_trips(self):
        end = endpoint()
        self.assertEqual(ServingEndpoint.from_dict(end.to_dict()), end)
        self.assertEqual(end.url("/v1/embeddings"), "http://localhost:8001/v1/embeddings")

    def test_invalid_url_and_trailing_slash_are_typed(self):
        with self.assertRaises(ServingError) as ctx:
            endpoint(base_url="not-a-url")
        self.assertEqual(ctx.exception.code, "INVALID_ENDPOINT")
        with self.assertRaises(ServingError):
            endpoint(base_url="http://localhost:8001/")

    def test_empty_fields_and_bad_timeout_are_typed(self):
        for bad in ({"name": ""}, {"model_id": " "}, {"timeout_s": 0}, {"timeout_s": -1}):
            with self.assertRaises(ServingError):
                endpoint(**bad)

    def test_path_must_be_absolute(self):
        with self.assertRaises(ServingError):
            endpoint().url("v1/embeddings")

    def test_from_dict_rejects_unknown_task_and_fields(self):
        data = endpoint().to_dict()
        data["task"] = "MYSTERY"
        with self.assertRaises(ServingError):
            ServingEndpoint.from_dict(data)
        extra = endpoint().to_dict()
        extra["unexpected"] = 1
        with self.assertRaises(ServingError):
            ServingEndpoint.from_dict(extra)


if __name__ == "__main__":
    unittest.main()
