import argparse
import os
from pathlib import Path

from cortex.core.connectors.google_drive import (
    GOOGLE_DRIVE_SCOPES,
    save_oauth_credentials,
)


def authorize(client_secrets: Path, token_file: Path, port: int) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, GOOGLE_DRIVE_SCOPES)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=port,
        open_browser=True,
        authorization_prompt_message="Opening Google authorization in your browser...",
        success_message="Authorization complete. You may close this window.",
    )
    save_oauth_credentials(credentials, token_file)
    print(f"Google OAuth token saved to {token_file.expanduser()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize Chimera Cortex for Google Drive.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorize_parser = subparsers.add_parser("authorize", help="Run Desktop OAuth authorization.")
    authorize_parser.add_argument(
        "--client-secrets",
        type=Path,
        default=Path(os.environ.get("GOOGLE_OAUTH_CLIENT_JSON", "client_secret.json")),
        help="Google OAuth Desktop client JSON (default: GOOGLE_OAUTH_CLIENT_JSON).",
    )
    authorize_parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(
            os.environ.get(
                "GOOGLE_OAUTH_TOKEN_FILE",
                "~/.config/chimera-cortex/google-drive-token.json",
            )
        ),
        help="Private authorized-user token file (default: GOOGLE_OAUTH_TOKEN_FILE).",
    )
    authorize_parser.add_argument("--port", type=int, default=0, help="Local callback port.")
    args = parser.parse_args()

    if args.command == "authorize":
        authorize(args.client_secrets.expanduser(), args.token_file.expanduser(), args.port)


if __name__ == "__main__":
    main()
