# Daisy V2 Discord Music Bot

Daisy V2 is the current Python Discord music bot for the Burmese music
catalogue. The old V1 bot files have been removed; active code lives in
`daisy_v2/`.

## Quick Start

```powershell
py -m venv .venv-v2
.venv-v2\Scripts\Activate.ps1
pip install -r requirements-v2.txt
Copy-Item .env.v2.example .env
python -m daisy_v2
```

Put your Discord bot token and server settings in `.env` before starting.

## Catalogue

Index the full Blogspot catalogue:

```powershell
python -m daisy_v2.indexer --all
```

Resume the same command if it is interrupted. The indexer saves progress as it
goes and stores the V2 catalogue in `data/daisy_v2/`.

## More Details

See [`daisy_v2/README.md`](daisy_v2/README.md) for V2 commands, deployment
notes, and catalogue migration details.
