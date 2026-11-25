"""Print per-task test totals, every skipped test with its reason, and every
test that needed a retry.

Input: /tmp/seal-test-events/*.tsv written by seal-test-logging.gradle, one line
per test execution: task, class, name, parent name, display name, result.

The build keeps upstream's `develocity.testRetry { maxRetries = 3; maxFailures = 5 }`
(build.gradle / micrometer-core/build.gradle), so a retried test is executed more
than once; a test counts as passed when one of its executions passed and as
failed only when every execution failed.

Gradle's test events carry no skip reason, so the reason is taken from the skip
annotations / assumptions on the test method (or its class, or the shared test
kit class it is inherited from) in the test source.
"""
import glob
import re

SKIP_MARKERS = re.compile(
    r"@Disabled|@Enabled(If|OnOs|OnJre|ForJreRange|IfSystemProperty|IfEnvironmentVariable)"
    r"|@DisabledOn|@DisabledIf|assume(True|False|That)|assumeThat"
)
ANNOTATION = re.compile(r'@\w+(?:\((?:[^()"]|"[^"]*")*\))?')
IDENT = re.compile(r"[A-Za-z_]\w*")

ALL_SOURCES = sorted(
    glob.glob("**/src/*/java/**/*.java", recursive=True) + glob.glob("**/src/*/kotlin/**/*.kt", recursive=True)
)


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def method_markers(path, method):
    """Skip annotations directly above `method` and assumptions in its body; None if absent."""
    lines = read_lines(path)
    sig = re.compile(r"\b(void|fun)\s+`?" + re.escape(method) + r"`?\s*\(")
    for i, line in enumerate(lines):
        if not sig.search(line):
            continue
        block = []
        j = i - 1
        while j >= 0 and lines[j].strip() and not lines[j].strip().endswith(("}", ";", "*/")):
            block.insert(0, lines[j].strip())
            j -= 1
        hits = [a for a in ANNOTATION.findall(" ".join(block)) if SKIP_MARKERS.search(a)]
        depth, started = 0, False
        for k in range(i, min(i + 80, len(lines))):
            if k > i and SKIP_MARKERS.search(lines[k]):
                hits.append(lines[k].strip())
            depth += lines[k].count("{") - lines[k].count("}")
            started = started or "{" in lines[k]
            if started and depth <= 0:
                break
        return hits
    return None


def class_markers(path):
    text = "\n".join(read_lines(path))
    head = re.split(r"\n(?:public |internal |abstract |open |final )*class ", text, maxsplit=1)[0]
    head = head.rsplit("*/", 1)[-1]
    found = [a for a in ANNOTATION.findall(" ".join(head.split())) if SKIP_MARKERS.search(a)]
    return [f"{a} (upstream class-level annotation)" for a in found]


def skip_reason(cls, name, parent):
    rel = cls.split("$")[0].replace(".", "/")
    m = IDENT.match(name) or IDENT.match(parent)
    method = m.group(0) if m else None
    own = [p for p in ALL_SOURCES if p.endswith("/" + rel + ".java") or p.endswith("/" + rel + ".kt")]
    hits = []
    for path in own:
        hits += class_markers(path)
        if method:
            found = method_markers(path, method)
            if found is not None:
                return "; ".join(dict.fromkeys(hits + found)) or "skipped by JUnit (no annotation in source)"
    if method:  # inherited from a shared test kit (e.g. micrometer-test's *VerificationTests)
        for path in ALL_SOURCES:
            found = method_markers(path, method)
            if found:
                return "; ".join(dict.fromkeys(hits + found)) + f" (inherited from {path})"
    return "; ".join(dict.fromkeys(hits)) or "skipped by JUnit (no annotation in source)"


tasks = {}  # task -> {(class, name, parent): {"runs": [...], "display": str}}
for tsv in sorted(glob.glob("/tmp/seal-test-events/*.tsv")):
    with open(tsv, encoding="utf-8") as fh:
        for raw in fh:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) != 6:
                continue
            task, cls, name, parent, display, result = parts
            entry = tasks.setdefault(task, {}).setdefault((cls, name, parent), {"runs": [], "display": display})
            entry["runs"].append(result)

grand = [0, 0, 0, 0]
failed_lines, flaky_lines, skipped_lines = [], [], []
for task in sorted(tasks):
    totals = [0, 0, 0, 0]  # tests, passed, failed, skipped
    for (cls, name, parent), entry in tasks[task].items():
        runs = entry["runs"]
        label = f"{cls} > {name}" + (f" ({entry['display']})" if entry["display"] not in (name, "") else "")
        totals[0] += 1
        if "SUCCESS" in runs:
            totals[1] += 1
            if "FAILURE" in runs:
                flaky_lines.append(
                    f"FLAKY TEST [{task}] {label} - failed {runs.count('FAILURE')}x, then passed on upstream develocity.testRetry"
                )
        elif "FAILURE" in runs:
            totals[2] += 1
            failed_lines.append(f"FAILED TEST [{task}] {label} - failed all {len(runs)} executions")
        else:
            totals[3] += 1
            skipped_lines.append(f"SKIPPED TEST [{task}] {label} - reason: {skip_reason(cls, name, parent)}")
    grand = [g + t for g, t in zip(grand, totals)]
    print(f"TEST RESULTS [{task}]: {totals[0]} tests, {totals[1]} passed, {totals[2]} failed, {totals[3]} skipped")

for line in failed_lines + flaky_lines + skipped_lines:
    print(line)
print(f"TEST RESULTS TOTAL: {grand[0]} tests, {grand[1]} passed, {grand[2]} failed, {grand[3]} skipped")
