# ISO 34504 → SAGA Converter

Converts ISO 34504 logical scenario YAMLs into SAGA batch prompts (one CSV row per scenario).
Non-scriptable variants (weather/lighting wording, ADS-failure scenarios) are filtered or sanitized automatically.

## Setup (first time only)

```bash
unzip iso34504_converter.zip
cd iso34504_converter
./setup.sh
```

The setup script checks Python, installs `pyyaml` + `anthropic`, and creates `.env` for you.

**API key:** if you use the GUI, you can skip editing any files — the first time you click **Convert**, it asks for your Anthropic API key and saves it to `.env` automatically. For CLI use, open `.env` in the converter folder and paste your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Requirements: Python 3.9+. For the GUI you also need tkinter (`sudo apt install python3-tk` on Ubuntu; usually preinstalled on macOS/Windows Python).

## Creating prompts

### Option A — GUI

```bash
python3 gui.py
```

1. **Add folder** → select the folder(s) containing your ISO 34504 YAMLs
2. Leave "Verify prompts" checked
3. Click **Convert**
4. The CSV appears in the converter folder, named after your input folder (e.g. `MyBatch_prompts.csv`)

Options:
- **Dry run** — extract + filter only, no API calls (works without an API key)
- **Include ego-action scenarios** — also generate prompts for ADS-failure scenarios that are skipped by default

### Option B — command line

```bash
# Whole folder
python3 converter.py --input /path/to/your_batch --verify -v

# Single file
python3 converter.py --input /path/to/SC-HWY-LF-0002_var01.yaml

# Multiple folders (separate with ':')
python3 converter.py --input /path/to/batchA:/path/to/batchB

# Custom output name
python3 converter.py --input /path/to/your_batch --output my_prompts.csv

# Dry run (no API key needed)
python3 converter.py --input /path/to/your_batch --dry-run
```

## Using the output

Open the CSV and copy the `prompt` column into SAGA's batch panel.

Each row includes:
- `scenario_id` / `parent_id` — traceability back to the source YAML
- `use_case_name`, `stpa_refs`, `risk_tier` — metadata from the source
- `prompt` — paste this into SAGA batch
- `verification_issues` — `OK`, or a list of things to fix manually

## What gets filtered out

- Weather / lighting / road-surface parameters — dropped from sweeps, wording stripped from prompts
- ADS-failure scenarios that can't be scripted (overspeeding, lane bias, nudge, unintended steering, tailgating) — skipped unless you pass `--include-ego-action`
- `set speed` / target-speed parameters — ADS targets, not scriptable knobs

## Troubleshooting

| Problem | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Edit `.env` in the converter folder, or `export ANTHROPIC_API_KEY=...` |
| `No module named 'yaml'` / `'anthropic'` | Re-run `./setup.sh` (or `pip install -r requirements.txt`) |
| `No module named 'tkinter'` | `sudo apt install python3-tk` (GUI only; CLI works without it) |
| 0 files found | Input folder must contain `.yaml` files with a top-level `scenario:` block |
