"""Auth-method selection used by the pipeline and CI workflows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from proptech import ci_auth

SA = "proptech-pipeline@proptech-housing.iam.gserviceaccount.com"
PROVIDER = "projects/123/locations/global/workloadIdentityPools/github/providers/github"


@pytest.mark.parametrize(
    ("has_key", "provider", "account", "project", "mode", "expected_project"),
    [
        (True, "", "", "", "key", ""),  # key carries its own project
        (True, "", "", "explicit", "key", "explicit"),
        (False, PROVIDER, SA, "", "wif", "proptech-housing"),  # derived from the email
        (True, PROVIDER, SA, "", "wif", "proptech-housing"),  # WIF wins over a lingering key
        (False, PROVIDER, SA, "other-proj", "wif", "other-proj"),
        (True, PROVIDER, "", "", "key", ""),  # half-configured WIF falls back to the key
        (False, "", SA, "", "none", ""),
        (False, "", "", "", "none", ""),
        (False, PROVIDER, "robot@example.com", "", "none", ""),  # no project derivable
    ],
)
def test_choose(
    has_key: bool, provider: str, account: str, project: str, mode: str, expected_project: str
) -> None:
    choice = ci_auth.choose(has_key, provider, account, project)
    assert (choice.mode, choice.project) == (mode, expected_project)
    assert choice.reason


def test_project_from_service_account() -> None:
    assert ci_auth.project_from_service_account(SA) == "proptech-housing"
    assert ci_auth.project_from_service_account("x@developer.gserviceaccount.com") == ""


def test_script_writes_github_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = {"HAS_KEY": "true", "WIF_PROVIDER": PROVIDER, "SERVICE_ACCOUNT": SA, "GITHUB_OUTPUT": str(out)}
    script = Path(ci_auth.__file__)
    subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True)
    lines = out.read_text().splitlines()
    assert "mode=wif" in lines
    assert "project=proptech-housing" in lines
