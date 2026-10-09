"""Pick the GitHub Actions auth method for BigQuery (stdlib only, so CI can run it bare).

Precedence: Workload Identity Federation when both WIF variables are set, otherwise the
``GCP_SA_KEY`` secret, otherwise nothing. ``google-github-actions/auth`` accepts exactly one
method, so the workflows pass only the selected one.

Run as ``python3 src/proptech/ci_auth.py``: it reads ``HAS_KEY``, ``WIF_PROVIDER``,
``SERVICE_ACCOUNT`` and ``PROJECT_ID`` from the environment and writes ``mode`` and
``project`` to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

_SA_EMAIL = re.compile(r"^[^@]+@(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com$")


@dataclass(frozen=True)
class AuthChoice:
    mode: str  # "wif" | "key" | "none"
    project: str  # empty means "use the project the credentials report"
    reason: str


def project_from_service_account(email: str) -> str:
    """``name@my-proj.iam.gserviceaccount.com`` -> ``my-proj`` (empty if not that shape)."""
    match = _SA_EMAIL.match(email.strip())
    return match["project"] if match else ""


def choose(has_key: bool, wif_provider: str, service_account: str, project_id: str) -> AuthChoice:
    provider, account, project = wif_provider.strip(), service_account.strip(), project_id.strip()
    if provider and account:
        derived = project or project_from_service_account(account)
        if not derived:
            return AuthChoice(
                "none",
                "",
                "WIF variables are set but no project: set GCP_PROJECT_ID or use a "
                "project-owned service account email",
            )
        return AuthChoice("wif", derived, "Workload Identity Federation (keyless)")
    if provider or account:
        half = "GCP_WORKLOAD_IDENTITY_PROVIDER" if provider else "GCP_SERVICE_ACCOUNT"
        if has_key:
            return AuthChoice("key", project, f"only {half} is set; falling back to GCP_SA_KEY")
        return AuthChoice("none", "", f"only {half} is set; WIF needs both variables")
    if has_key:
        return AuthChoice("key", project, "service-account key secret GCP_SA_KEY")
    return AuthChoice("none", "", "no GCP credentials configured (see docs/gcp_setup.md)")


def main() -> int:
    choice = choose(
        os.environ.get("HAS_KEY", "").lower() == "true",
        os.environ.get("WIF_PROVIDER", ""),
        os.environ.get("SERVICE_ACCOUNT", ""),
        os.environ.get("PROJECT_ID", ""),
    )
    lines = [f"mode={choice.mode}", f"project={choice.project}", f"reason={choice.reason}"]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
