from pathlib import Path
from unittest.mock import patch

import pytest

from src.sandbox.security import (
    SecurityFailureCode,
    WorkerSecurityViolation,
    expected_worker_environment,
    validate_worker_environment,
)


def test_darwin_environment_is_generated_without_inheriting_parent_settings():
    root = Path("/project")
    with patch("src.sandbox.security.sys.platform", "darwin"), \
            patch("src.sandbox.security.os.getuid", return_value=501), \
            patch.dict("os.environ", {"__CF_USER_TEXT_ENCODING": "untrusted", "API_SECRET": "secret"}):
        environment = expected_worker_environment(root)
        assert environment["__CF_USER_TEXT_ENCODING"] == "0x1F5:0:0"
        assert "API_SECRET" not in environment
        validate_worker_environment(environment, root)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("mutation", ["extra", "missing", "changed", "encoding"])
def test_startup_environment_still_rejects_every_unapproved_difference(platform, mutation):
    root = Path("/project")
    with patch("src.sandbox.security.sys.platform", platform):
        environment = expected_worker_environment(root)
        if mutation == "extra":
            environment["API_SECRET"] = "secret"
        elif mutation == "missing":
            environment.pop("PYTHONHASHSEED")
        elif mutation == "changed":
            environment["PYTHONPATH"] = "/untrusted"
        else:
            environment["__CF_USER_TEXT_ENCODING"] = "untrusted"
        with pytest.raises(WorkerSecurityViolation) as failure:
            validate_worker_environment(environment, root)
        assert failure.value.failure.code == SecurityFailureCode.ENVIRONMENT_INHERITANCE_DENIED.value


def test_linux_environment_keeps_the_existing_six_keys():
    with patch("src.sandbox.security.sys.platform", "linux"):
        assert set(expected_worker_environment(Path("/project"))) == {
            "LC_CTYPE", "PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONNOUSERSITE",
            "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
        }
