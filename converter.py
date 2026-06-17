"""
ISO 34504 → SAGA batch prompt converter.

Usage:
    python3 converter.py --input /path/to/Zhen_batch --output saga_prompts.csv
    python3 converter.py --input /path/to/file.yaml   # single file test
    python3 converter.py --input /path/to/Zhen_batch --dry-run  # no Haiku calls
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import yaml

# ── Filters ───────────────────────────────────────────────────────────────────

# Weather/lighting/road-surface are no longer a skip reason — those scenarios are
# kept and the wording is stripped from the generated prompt (see _SYSTEM rules).
# These param ids are still dropped from the sweep, since they aren't scriptable knobs.
# 'set speed' / target speed is an ADS target (not a scriptable ego motion) — dropped too.
_SKIP_PARAMS   = {"p_friction", "p_illumination", "p_road_surface", "p_visibility",
                  "p_set_speed", "p_target_speed", "p_desired_speed", "p_cruise"}

# ADS-failure scenarios: the failure IS the scenario — needs ego action, can't script it.
# Skipped by default; include with extract(..., include_ego_action=True).
_SKIP_PARENTS  = {
    "SC-HWY-LF-0005",  # overspeeding
    "SC-HWY-LF-0008",  # lane bias
    "SC-HWY-LF-0010",  # unintended steering
    "SC-HWY-LF-0011",  # nudge
    "SC-HWY-LF-0015",  # tailgating
}


# ── Extraction ────────────────────────────────────────────────────────────────

def extract(data: dict, include_ego_action: bool = False) -> dict:
    sc = data.get("scenario", {})
    if not sc:
        return {"skip": True, "reason": "no scenario block"}

    scenario_id = sc.get("scenario_id", "")
    parent_id   = sc.get("parent_scenario_id", "")

    ego_action = any(parent_id.startswith(s) or scenario_id.startswith(s) for s in _SKIP_PARENTS)
    if ego_action and not include_ego_action:
        return {"skip": True, "reason": "ADS-failure / ego-action (unscriptable)", "scenario_id": scenario_id}

    odd = sc.get("odd_context", {})

    scenery    = odd.get("scenery", {})
    taxonomy   = sc.get("taxonomy", {})
    trace      = sc.get("traceability", {})
    criticality= sc.get("criticality", {})
    dyn        = sc.get("dynamic_behavior", {})

    actors = [
        {
            "id":      a.get("actor_id", ""),
            "type":    a.get("actor_type", ""),
            "role":    a.get("role", ""),
            "vehicle": a.get("vehicle_configuration", ""),
        }
        for a in sc.get("actors", [])
    ]

    params = []
    for p in sc.get("parameter_space", {}).get("parameters", []):
        pid = p.get("id", "")
        if any(pid.startswith(s) for s in _SKIP_PARAMS):
            continue
        if p.get("sensitivity_class", "") == "non_critical":
            continue
        dist = p.get("distribution", {})
        params.append({
            "id":          pid,
            "name":        p.get("name", ""),
            "unit":        p.get("unit", ""),
            "sensitivity": p.get("sensitivity_class", ""),
            "dist":        dist,
        })

    phases = [
        {"name": ph.get("phase_name", ""), "desc": ph.get("description", ""), "dur_s": ph.get("duration_s", 0)}
        for ph in dyn.get("phases", [])
    ]

    return {
        "skip":         False,
        "ego_action":   ego_action,
        "scenario_id":  scenario_id,
        "parent_id":    parent_id,
        "name":         sc.get("name", ""),
        "description":  sc.get("description", ""),
        "use_case_name":taxonomy.get("use_case_name", ""),
        "stpa_refs":    trace.get("stpa_refs", []),
        "risk_tier":    criticality.get("risk_tier", ""),
        "lane_count":   scenery.get("lane_count", 3),
        "geometry":     scenery.get("road_geometry", "straight"),
        "speed_limit":  scenery.get("speed_limit_kph", 90),
        "actors":       actors,
        "params":       params,
        "phases":       phases,
        "trigger":      dyn.get("trigger_condition", ""),
        "expected":     dyn.get("expected_ads_response", ""),
    }


# ── Distribution formatting (tolerant of malformed source YAML) ────────────────

def _dist_range(dist: dict):
    """Return (lo, hi) strings for a uniform distribution, else (None, None).

    Tolerates malformed 'uniform' distributions that omit min/max (e.g. a single
    fixed 'value'/'Value: 0' typed as uniform) instead of raising KeyError.
    """
    if not isinstance(dist, dict) or dist.get("type") != "uniform":
        return None, None
    lo, hi = dist.get("min"), dist.get("max")
    if lo is None and hi is None:
        v = dist.get("value", dist.get("Value"))
        return (str(v), str(v)) if v is not None else (None, None)
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return str(lo), str(hi)


def _fmt_dist(dist: dict, unit: str) -> str:
    lo, hi = _dist_range(dist)
    if lo is not None:
        return f"{lo}–{hi} {unit}" if lo != hi else f"{lo} {unit}"
    return str(dist.get("values", "")) if isinstance(dist, dict) else ""


# ── Few-shot golden examples ───────────────────────────────────────────────────

_GOLDEN = [
    {
        "name": "Ego cuts off acceleration — adjacent vehicle cuts in from behind",
        "prompt": (
            "Create a scenario where ego truck is cruising at 55 kph on a 2-lane highway. "
            "A passenger car in the adjacent right lane is travelling at 90 kph and initiates "
            "a lane change into ego's lane from behind, starting with a 10m gap to ego's rear. "
            "The lane change completes in 0.8 seconds. Use TTC trigger.\n"
            "Add tag \"source_SC-HWY-LF-0002, sotif\"\n"
            "Sweep: ego speed 55–65 kph, actor speed 60–90 kph, cut-in gap 10–20 m."
        ),
    },
    {
        "name": "Ego stops in lane — multiple HGV followers",
        "prompt": (
            "Create a scenario where ego truck is cruising at 80 kph on a 4-lane highway with no lead vehicle. "
            "Ego decelerates at 5 m/s² and comes to a full stop in the travel lane. "
            "A passenger car follows 30m behind at 90 kph and a heavy truck follows 80m behind at 80 kph. "
            "The gap closes as ego stops.\n"
            "Add tag \"source_SC-HWY-LF-0007, sotif\"\n"
            "Sweep: ego initial speed 80–90 kph, ego decel rate 2–5 m/s², following car speed 70–90 kph, initial gap 30–100 m."
        ),
    },
    {
        "name": "Oversize load lead vehicle",
        "prompt": (
            "Create a scenario where ego truck is following a lead vehicle on a 3-lane highway at 85 kph with a 10m gap. "
            "The lead vehicle is carrying an oversize load with long pipes protruding from its rear "
            "and travelling at 55 kph. The gap is closing.\n"
            "Add tag \"source_SC-HWY-LF-0013, sotif\"\n"
            "Sweep: initial gap 10–80 m, ego speed 80–90 kph."
        ),
    },
    {
        "name": "Lead hard brakes — adjacent vehicle blocks escape lane change",
        "prompt": (
            "Create a scenario where ego truck is following a lead vehicle on a 3-lane highway at 85 kph with a 10m gap. "
            "The lead vehicle brakes hard at 5 m/s². "
            "A vehicle in the adjacent right lane at 85 kph blocks any evasive lane change. The gap closes.\n"
            "Add tag \"source_SC-HWY-LF-0014, sotif\"\n"
            "Sweep: initial gap 10–80 m, lead decel rate 2–5 m/s², adjacent vehicle speed 80–90 kph."
        ),
    },
]

_SYSTEM = (
    "You convert ISO 34504 logical scenario metadata into SAGA simulation prompts.\n"
    "SAGA generates ADP Simian .scn.yaml files from plain English prompts.\n\n"
    "Rules:\n"
    "- ALWAYS begin the prompt with 'Create a scenario where ' followed by the scenario description.\n"
    "- Describe ONLY concrete, scriptable physical actions: what ego does (speed, accel/decel, lane position), what each actor does (speed, lane, gap, lane change, brake), and where (lane count, geometry).\n"
    "- NEVER describe the autonomous driving system, ADS, ADAS, perception, sensors, or any system fault/failure. Do NOT write 'the ADS fails to...', 'system does not command...', 'fails to detect', etc.\n"
    "- NEVER use the words 'fail', 'fails', or 'fails to' anywhere. Express the behavior as positive motion: write 'ego does not accelerate' or 'ego remains at a constant reduced speed', NOT 'ego fails to accelerate'.\n"
    "- TRANSLATE any ADS-failure framing into the resulting observable EGO MOTION. e.g. 'ADS fails to command acceleration / ego remains at reduced speed' becomes 'ego cruises at a constant reduced speed'. Describe the motion, never the system that caused it.\n"
    "- Keep it terse: state the INITIAL actions only — ego's speed + lane + geometry, and each actor's speed + position + approach. A brief consequence like 'the gap closes' is allowed, but do NOT narrate the expected response, recovery, or safety outcome ('forcing the follower to brake hard', 'creating collision risk', 'restores safe headway').\n"
    "- NEVER mention a 'set speed', 'target speed', 'desired speed', or 'cruise control speed'. Those are ADS targets, not scriptable. State only ego's actual cruising speed (or its swept range).\n"
    "- NEVER add ego control instructions such as 'do not add adaptive cruise to ego' or 'ego maintains constant speed'. Just state ego's actual speed.\n"
    "- State ego's speed ONCE in the opening clause. Do NOT restate it later ('ego maintains its reduced speed of 60 kph', 'ego remains at 60 kph'). Mention ego again only if it changes (decelerates, stops, changes lane).\n"
    "- Set fixed values at the CRITICAL BOUNDARY (worst case): smallest gaps, highest speed deltas, harshest decel.\n"
    "- Always end with 'Sweep: ...' listing critical parameters and ranges. Do NOT include 'set speed' as a sweep parameter.\n"
    "- For cut-in from BEHIND: write 'Use TTC trigger.'\n"
    "- For traceability, do NOT write a 'Source: ...' sentence. Instead add exactly one line right before the Sweep line: Add tag \"source_SC-HWY-LF-XXXX, sotif\" (replace XXXX with the scenario's parent id, keep the quotes).\n"
    "- NEVER add a 'source:' field.\n"
    "- Under 100 words. One short paragraph + the tag line + the sweep line. No markdown. Output prompt only.\n"
    "- NEVER mention weather, rain, wet road, friction, lighting, visibility, glare, lane markings, lane-marking color/type, degraded markings, or road surface condition — those are not scriptable.\n"
    "- ALWAYS state the lane count explicitly: '2-lane highway', '3-lane highway', '4-lane highway'.\n\n"
    "Examples:\n\n"
    + "\n\n".join(
        f"Scenario: {ex['name']}\nPrompt: {ex['prompt']}"
        for ex in _GOLDEN
    )
)


# ── Haiku call ────────────────────────────────────────────────────────────────

# Phrases that signal ADS-fault framing or non-scriptable ODD conditions.
# Sentences containing these are dropped from the text fed to Haiku so they
# don't get echoed into the prompt.
_FAULT_MARKERS = (
    "autonomous driving system", "ads ", "adas", "perception", "sensor",
    "does not command", "does not issue", "fails to command",
    "fails to issue", "fails to detect", "no command",
    "lane marking", "lane_marking", "degraded marking", "road surface",
    "road_surface", "friction", "illumination", "visibility", "glare",
)


def _sanitize_text(text: str) -> str:
    """Drop sentences that reference ADS faults or non-scriptable ODD conditions."""
    if not text:
        return text
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not any(m in s.lower() for m in _FAULT_MARKERS)]
    return " ".join(kept).strip()


def generate_prompt(ex: dict) -> str:
    import anthropic
    client = anthropic.Anthropic()

    description = _sanitize_text(ex["description"])
    trigger     = _sanitize_text(ex["trigger"])
    phases      = [ph for ph in ex["phases"] if _sanitize_text(ph["desc"])]

    user_msg = (
        f"Scenario ID: {ex['scenario_id']}\n"
        f"Name: {ex['name']}\n"
        f"Description: {description}\n"
        f"Use case: {ex['use_case_name']}\n"
        f"Lanes: {ex['lane_count']}, geometry: {ex['geometry']}, speed limit: {ex['speed_limit']} kph\n"
        f"Risk tier: {ex['risk_tier']}\n\n"
        f"Actors:\n{json.dumps(ex['actors'], indent=2)}\n\n"
        f"Critical parameters:\n"
        + "\n".join(
            f"  {p['id']} ({p['name']}): " + _fmt_dist(p['dist'], p['unit'])
            for p in ex['params']
            if p['sensitivity'] in ('critical', '')
        )
        + f"\n\nPhases:\n" + "\n".join(f"  {ph['name']} ({ph['dur_s']}s): {_sanitize_text(ph['desc'])}" for ph in phases)
        + f"\n\nTrigger: {trigger}"
    )

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text.strip()


# ── Verification helpers ──────────────────────────────────────────────────────

def verify_prompt(prompt: str, ex: dict) -> list[str]:
    """Check that the generated prompt covers the key requirements from the source YAML."""
    issues = []

    # Must reference scenario ID
    if ex["scenario_id"].split("_")[0] not in prompt and ex["parent_id"] not in prompt:
        issues.append(f"Missing source tag for {ex['scenario_id']}")

    # Must have sweep line
    if "sweep" not in prompt.lower():
        issues.append("No 'Sweep:' line")

    # Critical params should appear
    for p in ex["params"]:
        if p["sensitivity"] != "critical":
            continue
        lo, hi = _dist_range(p["dist"])
        if lo is not None and lo not in prompt and hi not in prompt:
            issues.append(f"Critical param {p['id']} range ({lo}–{hi}) not in prompt")

    # Lane count should be mentioned if non-standard
    if ex["lane_count"] == 2 and "2-lane" not in prompt and "two-lane" not in prompt.lower():
        issues.append("2-lane road not mentioned")

    # No ADS-fault / non-scriptable ODD wording should leak into the prompt
    pl = prompt.lower()
    leaked = [w for w in ("fail", "autonomous driving system", "ads ", "perception",
                          "sensor", "lane marking", "road surface", "set speed",
                          "target speed", "adaptive cruise")
              if w in pl]
    if leaked:
        issues.append(f"Leaked non-scriptable wording: {', '.join(leaked)}")

    return issues


# ── CLI ───────────────────────────────────────────────────────────────────────

def _load_env():
    """Load ANTHROPIC_API_KEY from a .env file next to this script.

    A real environment variable always takes precedence over the file.
    """
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main():
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",    required=True, help="YAML file, directory, or multiple paths separated by ':'")
    ap.add_argument("--output",   default=None, help="Output CSV (default: <folder_name>_prompts.csv)")
    ap.add_argument("--dry-run",  action="store_true", help="Extract only, no Haiku")
    ap.add_argument("--verify",   action="store_true", help="Run verification checks")
    ap.add_argument("--include-ego-action", action="store_true",
                    help="Also generate ADS-failure / ego-action scenarios (skipped by default)")
    ap.add_argument("--verbose",  "-v", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is not set.\n"
            "Create a .env file next to converter.py (see .env.example) "
            "or export the variable, then re-run.\n"
            "Tip: use --dry-run to test without an API key."
        )

    # Support multiple paths separated by ":"
    input_paths = [Path(p.strip()) for p in args.input.split(":") if p.strip()]
    files = []
    for root in input_paths:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.yaml")))

    # Auto-name output from folder name(s)
    if args.output:
        out = Path(args.output)
    else:
        names = [p.name for p in input_paths]
        out = Path("_".join(names) + "_prompts.csv")

    print(f"Found {len(files)} YAML file(s) across {len(input_paths)} path(s)")

    rows, skipped = [], []

    for fpath in files:
        try:
            with open(fpath) as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(f"  PARSE ERROR {fpath.name}: {e}", file=sys.stderr)
            continue

        if not isinstance(data, dict) or "scenario" not in data:
            continue

        ex = extract(data, include_ego_action=args.include_ego_action)
        if ex is None:
            continue

        if ex.get("skip"):
            skipped.append((fpath.name, ex.get("scenario_id", "?"), ex.get("reason", "?")))
            if args.verbose:
                print(f"  SKIP {fpath.name}: {ex.get('reason')}")
            continue

        if args.dry_run:
            prompt = "[DRY RUN]"
        else:
            if args.verbose:
                print(f"  Generating: {ex['scenario_id']}...")
            prompt = generate_prompt(ex)

        verification_issues = verify_prompt(prompt, ex) if args.verify else []

        rows.append({
            "source_file":       str(fpath),
            "scenario_id":       ex["scenario_id"],
            "parent_id":         ex["parent_id"],
            "use_case_name":     ex["use_case_name"],
            "stpa_refs":         ", ".join(ex.get("stpa_refs", [])),
            "risk_tier":         ex["risk_tier"],
            "lane_count":        ex["lane_count"],
            "geometry":          ex["geometry"],
            "ego_action":        "yes" if ex.get("ego_action") else "",
            "prompt":            prompt,
            "verification_issues": "; ".join(verification_issues) if verification_issues else "OK",
        })

        if args.verbose and verification_issues:
            print(f"    VERIFICATION ISSUES: {verification_issues}")

    fieldnames = ["source_file", "scenario_id", "parent_id", "use_case_name",
                  "stpa_refs", "risk_tier", "lane_count", "geometry",
                  "ego_action", "prompt", "verification_issues"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} prompt(s) → {out}")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for name, scid, reason in skipped:
            print(f"  {scid} ({name}): {reason}")


if __name__ == "__main__":
    main()
