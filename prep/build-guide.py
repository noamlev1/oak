#!/usr/bin/env python3
"""Build the Oak workflow field guide.

Reads every guide-data/<id>.json, validates it against GUIDE-SCHEMA.md, and
inlines the data into field-guide.src.html in place of the one placeholder
line `/*__GUIDE_DATA__*/`, writing field-guide.html.

Usage:
  python3 build-guide.py                       # default dirs below
  python3 build-guide.py --data-dir DIR --out FILE [--src FILE] [--allow-empty]

Exit code 1 on any validation error (missing required field, node count
mismatch, duplicate or non-contiguous steps, duplicate node names, bad lane/id).
Warnings (em/en dashes auto-replaced, branch targets that are not node names,
expected workflows with no file yet) never fail the build.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER = "/*__GUIDE_DATA__*/"
ORDER = ["00", "01", "02", "02A", "03", "04", "05", "06", "07", "08", "99"]
LANES = {"intake", "enrich", "write", "rep", "ops"}

TOP_REQUIRED = {
    "id": str, "name": str, "short": str, "lane": str, "one_breath": str,
    "trigger": str, "input": str, "output": str, "called_by": list,
    "calls": list, "node_count": int, "groups": list, "decisions": list,
    "questions": list,
}
GROUP_REQUIRED = {"name": str, "purpose": str, "nodes": list}
# (type or tuple of types). None allowed only where the schema says "or null".
NODE_REQUIRED = {
    "step": int, "name": str, "type": str, "what": str, "why": str,
    "in": str, "out": str, "decides": (str, type(None)),
    "code": (str, type(None)), "branches": list, "on_failure": str,
    "say_it": str, "next": list,
}
BRANCH_REQUIRED = {"label": str, "goes_to": str, "meaning": str}
DECISION_REQUIRED = {"title": str, "why": str}
QUESTION_REQUIRED = {"q": str, "a": str}

DASHES = {"\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2015": "-"}


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def check_fields(obj, spec, where, rep):
    if not isinstance(obj, dict):
        rep.err(where, f"expected an object, got {type(obj).__name__}")
        return False
    ok = True
    for key, typ in spec.items():
        if key not in obj:
            rep.err(where, f"missing required field '{key}'")
            ok = False
            continue
        val = obj[key]
        # bool is an int subclass; never accept it as a number
        if typ is int and (isinstance(val, bool) or not isinstance(val, int)):
            rep.err(where, f"'{key}' must be an integer, got {val!r}")
            ok = False
        elif typ is not int and not isinstance(val, typ):
            rep.err(where, f"'{key}' has wrong type {type(val).__name__}")
            ok = False
        elif isinstance(val, str) and not val.strip() and key not in ("decides", "code"):
            rep.err(where, f"'{key}' is empty")
            ok = False
    return ok


def fix_dashes(value, where, rep, path="$"):
    """Replace em/en dashes everywhere (schema forbids them) and warn."""
    if isinstance(value, str):
        out = value
        for bad, good in DASHES.items():
            if bad in out:
                out = out.replace(bad, good)
        if out != value:
            rep.warn(where, f"replaced em/en dash at {path}")
        return out
    if isinstance(value, list):
        return [fix_dashes(v, where, rep, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict):
        return {k: fix_dashes(v, where, rep, f"{path}.{k}") for k, v in value.items()}
    return value


def validate(doc, fname, rep):
    where = fname
    if not check_fields(doc, TOP_REQUIRED, where, rep):
        return
    wid = doc["id"]
    if wid not in ORDER:
        rep.err(where, f"id '{wid}' is not one of {ORDER}")
    stem = os.path.splitext(os.path.basename(fname))[0]
    if stem.upper() != str(wid).upper():
        rep.err(where, f"file name '{stem}' does not match id '{wid}'")
    if doc["lane"] not in LANES:
        rep.err(where, f"lane '{doc['lane']}' is not one of {sorted(LANES)}")

    nodes = []
    for gi, g in enumerate(doc["groups"]):
        gw = f"{where} groups[{gi}]"
        if not check_fields(g, GROUP_REQUIRED, gw, rep):
            continue
        for ni, n in enumerate(g["nodes"]):
            nw = f"{gw} '{g['name']}' nodes[{ni}]"
            if isinstance(n, dict) and isinstance(n.get("name"), str):
                nw = f"{where} node '{n['name']}'"
            if not check_fields(n, NODE_REQUIRED, nw, rep):
                continue
            for bi, b in enumerate(n["branches"]):
                check_fields(b, BRANCH_REQUIRED, f"{nw} branches[{bi}]", rep)
            for x in n["next"]:
                if not isinstance(x, str):
                    rep.err(nw, f"'next' entries must be strings, got {x!r}")
            nodes.append(n)

    if len(nodes) != doc["node_count"]:
        rep.err(where, f"node_count is {doc['node_count']} but groups hold {len(nodes)} nodes")

    names = [n["name"] for n in nodes]
    dup_names = sorted({x for x in names if names.count(x) > 1})
    if dup_names:
        rep.err(where, f"node names appear more than once: {dup_names}")

    steps = [n["step"] for n in nodes]
    dup_steps = sorted({s for s in steps if steps.count(s) > 1})
    if dup_steps:
        rep.err(where, f"duplicate step numbers: {dup_steps}")
    if nodes and sorted(set(steps)) != list(range(1, len(nodes) + 1)):
        missing = sorted(set(range(1, len(nodes) + 1)) - set(steps))
        extra = sorted(set(steps) - set(range(1, len(nodes) + 1)))
        rep.err(where, f"steps must run 1..{len(nodes)}; missing {missing}, out of range {extra}")

    known = set(names)
    for n in nodes:
        for b in n["branches"]:
            if isinstance(b, dict) and b.get("goes_to") not in known:
                rep.warn(where, f"node '{n['name']}' branch '{b.get('label')}' goes to "
                                f"'{b.get('goes_to')}', which is not a node in this workflow")
        for x in n["next"]:
            if isinstance(x, str) and x not in known:
                rep.warn(where, f"node '{n['name']}' next '{x}' is not a node in this workflow")

    for i, d in enumerate(doc["decisions"]):
        check_fields(d, DECISION_REQUIRED, f"{where} decisions[{i}]", rep)
    for i, q in enumerate(doc["questions"]):
        check_fields(q, QUESTION_REQUIRED, f"{where} questions[{i}]", rep)
    if not doc["decisions"]:
        rep.warn(where, "no decisions")
    if not doc["questions"]:
        rep.warn(where, "no questions")


def sort_key(path):
    stem = os.path.splitext(os.path.basename(path))[0].upper()
    return (ORDER.index(stem), stem) if stem in ORDER else (len(ORDER), stem)


def script_safe_json(data):
    """JSON that is safe to drop inside an inline <script>."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # "</" would let a string like "</script>" close the element; "<!--" can
    # switch the parser into script-data-escaped state. Both are valid JSON
    # escapes when written as \u003c. U+2028/2029 are line terminators in
    # older JS engines.
    s = s.replace("<", "\\u003c").replace(">", "\\u003e")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "guide-data"))
    ap.add_argument("--src", default=os.path.join(HERE, "field-guide.src.html"))
    ap.add_argument("--out", default=os.path.join(HERE, "field-guide.html"))
    ap.add_argument("--allow-empty", action="store_true", help="build even when no data files exist")
    args = ap.parse_args()

    files = sorted(
        (os.path.join(args.data_dir, f) for f in os.listdir(args.data_dir) if f.lower().endswith(".json")),
        key=sort_key,
    ) if os.path.isdir(args.data_dir) else []

    rep = Report()
    guide = []
    seen = set()
    for path in files:
        fname = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            rep.err(fname, f"cannot read JSON: {exc}")
            continue
        doc = fix_dashes(doc, fname, rep)
        validate(doc, fname, rep)
        wid = doc.get("id") if isinstance(doc, dict) else None
        if wid in seen:
            rep.err(fname, f"id '{wid}' appears in more than one file")
        seen.add(wid)
        guide.append(doc)

    stems = {os.path.splitext(os.path.basename(p))[0].upper() for p in files}
    for wid in ORDER:
        if wid not in stems:
            rep.warn("coverage", f"no data file for {wid} yet (the page shows it as not written)")
    for p in files:
        if sort_key(p)[0] == len(ORDER):
            rep.warn(os.path.basename(p), "unexpected file name; included after the known workflows")

    for w in rep.warnings:
        print(f"WARN  {w}", file=sys.stderr)
    if rep.errors:
        for e in rep.errors:
            print(f"ERROR {e}", file=sys.stderr)
        print(f"\nBuild FAILED: {len(rep.errors)} error(s). Nothing written.", file=sys.stderr)
        return 1
    if not guide and not args.allow_empty:
        print(f"Build FAILED: no *.json files in {args.data_dir}. Pass --allow-empty to build anyway.",
              file=sys.stderr)
        return 1

    with open(args.src, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.split("\n")
    hits = [i for i, ln in enumerate(lines) if ln.strip() == PLACEHOLDER]
    if len(hits) != 1:
        print(f"Build FAILED: expected exactly one line '{PLACEHOLDER}' in {args.src}, found {len(hits)}",
              file=sys.stderr)
        return 1
    indent = lines[hits[0]][: len(lines[hits[0]]) - len(lines[hits[0]].lstrip())]
    lines[hits[0]] = f"{indent}window.GUIDE = {script_safe_json(guide)};"
    out = "\n".join(lines)

    for bad in DASHES:
        if bad in out:
            print(f"WARN  page source contains {bad!r} (em/en dash)", file=sys.stderr)

    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, args.out)

    total_nodes = sum(d["node_count"] for d in guide)
    ids = ", ".join(d["id"] for d in guide)
    print(f"Built {args.out}: {len(guide)} workflow(s) [{ids}], {total_nodes} nodes, "
          f"{len(out) / 1024:.0f} KB, {len(rep.warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
