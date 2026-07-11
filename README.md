# chimera-cortex
Chimera Cortex: An Omni-Context Knowledge Engine

## Google Drive Desktop OAuth

Install dependencies, then authorize a Google OAuth Desktop client:

```bash
pip install -r requirements.txt
python google_auth.py authorize \
  --client-secrets /path/to/client_secret.json \
  --token-file ~/.config/chimera-cortex/google-drive-token.json
```

Set the token path in the service environment:

```bash
GOOGLE_OAUTH_TOKEN_FILE=~/.config/chimera-cortex/google-drive-token.json
```

Create the Google Drive source with a credential reference, never a token value:

```json
{
  "provider": "google_drive",
  "folder_id": "your-folder-id",
  "oauth_token_file_env": "GOOGLE_OAUTH_TOKEN_FILE"
}
```

The connector requests read-only Drive access. Expired access tokens are refreshed
and atomically persisted with owner-only (`0600`) permissions.
