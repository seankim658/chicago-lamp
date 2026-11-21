# Chicago LAMP

Chicago LAMP is an open-source archive of observed CTA bus and 'L' train stop events. It polls CTA's public real-time feeds, keeps every raw response, and turns them into daily Parquet files of arrivals, departures and travel times. It is modelled on the MBTA's LAMP, and its published files follow LAMP's column names where CTA data allows.

Data used by this project is provided by the Chicago Transit Authority and is subject to CTA's Developer License Agreement. This project is not affiliated with or endorsed by CTA.

## Layout

The repository is a uv workspace. Each package lives at `packages/<layer>/<name>`, and its layer decides what it may import.

| Layer    | Packages                       | May import            |
| -------- | ------------------------------ | --------------------- |
| `shared` | `lamp-storage`, `lamp-schemas` | Nothing in the repo   |
| `stages` | `lamp-collector`               | Only `shared` packages |

Packages in the same layer never import each other. The check script enforces this.

## Getting CTA keys

The collector needs two keys, and CTA issues them separately.

1. **Bus Tracker key.** Create an account on CTA Bus Tracker, then apply under "Developer API" in your account. See [CTA's Bus Tracker developer page](https://www.transitchicago.com/developers/bustracker/).
2. **Developer Center key.** This key covers Train Tracker and the GTFS-Realtime feeds. Apply through [CTA's Developer Center](https://www.transitchicago.com/developers/traintracker/).

Both keys come with a default limit of 100,000 calls a day. Copy `.env.example` to `.env` and fill in the keys. Never commit `.env`, and never paste a key into an issue, a log or a test fixture.

## Development

Install [uv](https://docs.astral.sh/uv/), then set up the workspace.

```bash
uv lock
uv sync --all-packages
```

Run every check before you push. It runs linting, formatting, type checking, dependency checks, import rules and tests, and it never changes a file.

```bash
bash scripts/check.sh            # everything
bash scripts/check.sh --packages lamp-collector   # faster, but not a full check
bash scripts/check.sh --help     # what each option does
```

CI runs the same script on every push and pull request.
