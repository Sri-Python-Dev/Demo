# secrets/

Put local credentials here. Everything in this folder except this README and `.gitkeep`
is git-ignored, so these files are never committed.

| File | What it is |
|---|---|
| `gmail_credentials.json` | OAuth client (Desktop app) downloaded from Google Cloud Console |
| `gmail_token.json` | Created automatically by `python -m app.email.gmail_client --authorize` |
