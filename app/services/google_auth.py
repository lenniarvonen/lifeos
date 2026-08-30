"""Shared Google OAuth plumbing, used by both Calendar and Gmail.

Run this module directly on the host (not inside Docker) once, to complete the
OAuth browser consent flow and produce the token file that gets mounted into
the container for all subsequent runs:

    cd app && python3 -m services.google_auth --authorize
"""
import argparse
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def load_credentials(client_secret_path: str, token_path: str) -> Credentials:
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise RuntimeError(
            f"No valid Google credentials at {token_path}. "
            "Run `python3 -m services.google_auth --authorize` on the host first."
        )

    return creds


def authorize(client_secret_path: str, token_path: str) -> None:
    """One-time interactive flow, run on the host (opens a browser)."""
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Saved token to {token_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--client-secret", default=os.environ.get("GOOGLE_CLIENT_SECRET_PATH", "../secrets/google_client_secret.json"))
    parser.add_argument("--token-path", default=os.environ.get("GOOGLE_TOKEN_PATH", "../secrets/google_token.json"))
    args = parser.parse_args()

    if args.authorize:
        authorize(args.client_secret, args.token_path)
    else:
        print("Nothing to do. Pass --authorize to run the one-time OAuth flow.", file=sys.stderr)
        sys.exit(1)
