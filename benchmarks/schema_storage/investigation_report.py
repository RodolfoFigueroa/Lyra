"""Summarize preserved observations without introducing performance thresholds."""

import argparse
import json
import operator
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np


def load_timings(directory: Path) -> dict:
    """Return valid complete batches grouped by case and operation."""
    invalid = invalid_cases(directory)
    grouped = defaultdict(list)
    for path in sorted(directory.glob("*-timing-*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        if (
            batch["status"] in {"passed", "stopped"}
            and batch["case"]["name"] not in invalid
        ):
            for row in batch["records"]:
                grouped[batch["case"]["name"], row["operation"]].append(row)
    return dict(grouped)


def invalid_cases(directory: Path) -> set[str]:
    """Return cases with failed or unfinished correctness-bearing checkpoints."""
    invalid = set()
    for pattern in ("*-timing-*.json", "*-profile-0.json", "*-memory-0.json"):
        for path in directory.glob(pattern):
            batch = json.loads(path.read_text(encoding="utf-8"))
            if batch["status"] not in {"passed", "stopped"}:
                invalid.add(batch["case"]["name"])
    return invalid


def summary(rows: list[dict], field: str = "seconds") -> dict:
    """Return observed statistics without interpolated production guarantees."""
    values = [row[field] for row in rows]
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "p95": float(np.quantile(values, 0.95)),
        "min": min(values),
        "max": max(values),
    }


def cache_mix(grouped: dict, case: str) -> dict:
    """Return weighted means, keeping CPU cost distinct from elapsed time."""
    modes = [
        grouped[case, f"graph:shared:{mode}"]
        for mode in ("cold", "full", "intermediate")
    ]
    return {
        label: {
            field: sum(
                weight * mean(row[field] for row in rows)
                for weight, rows in zip(weights, modes, strict=True)
            )
            for field in ("seconds", "cpu_seconds")
        }
        for label, weights in {
            "fresh-heavy": (0.8, 0.1, 0.1),
            "full-hit-heavy": (0.1, 0.8, 0.1),
            "intermediate-heavy": (0.1, 0.1, 0.8),
        }.items()
    }


def attribution(row: dict) -> str:
    """Return exclusive-time categories; unassigned work remains visible."""
    file, name = row["file"], row["function"]
    if "_hashlib" in name:
        return "digest"
    if "content_fingerprints.py" in file:
        return "fingerprint Python/canonicalization"
    for pattern, label in {
        "pandera": "Pandera",
        "arrow_ownership.py": "ownership Python",
        "arrow_conversion.py": "conversion Python",
        "geometry": "geometry Python",
        "ipc_codec.py": "IPC/integrity Python",
    }.items():
        if pattern in file:
            return label
    if "read" in name or "write" in name:
        return "read/write calls (includes memory buffers)"
    return "other/native/dispatch"


def render(directory: Path) -> None:
    """Write reproducible tables and machine-readable derived summaries."""
    grouped = load_timings(directory)
    case_names = sorted({key[0] for key in grouped})
    derived = {
        "timings": [
            {
                "case": case,
                "operation": operation,
                **summary(rows),
                "cpu_mean": mean(row["cpu_seconds"] for row in rows),
            }
            for (case, operation), rows in grouped.items()
        ],
        "cache_mixes": {
            case: cache_mix(grouped, case)
            for case in case_names
            if all(
                (case, f"graph:shared:{mode}") in grouped
                for mode in ("cold", "full", "intermediate")
            )
        },
    }
    lines = [
        "# Storage investigation measurements",
        "",
        (
            "Times are milliseconds; p95 is an empirical sample percentile. Raw "
            "samples remain authoritative."
        ),
        "",
        "| Case | Operation | n | Median | p95 | Min-max | CPU mean |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row['case']} | "
        f"{row['operation']} | "
        f"{row['n']} | "
        f"{row['median'] * 1000:.3f} | "
        f"{row['p95'] * 1000:.3f} | "
        f"{row['min'] * 1000:.3f}-{row['max'] * 1000:.3f} | "
        f"{row['cpu_mean'] * 1000:.3f} |"
        for row in derived["timings"]
    )
    lines += [
        "",
        "## Illustrative cache mixes",
        "",
        (
            "Weighted mean elapsed / CPU milliseconds; these are neither weighted "
            "percentiles nor concurrency predictions."
        ),
        "",
        "| Case | Fresh-heavy | Full-hit-heavy | Intermediate-heavy |",
        "| --- | ---: | ---: | ---: |",
    ]
    for case, mixes in derived["cache_mixes"].items():
        cells = [
            f"{mixes[label]['seconds'] * 1000:.2f} / "
            f"{mixes[label]['cpu_seconds'] * 1000:.2f}"
            for label in ("fresh-heavy", "full-hit-heavy", "intermediate-heavy")
        ]
        lines.append(f"| {case} | " + " | ".join(cells) + " |")
    derived["profiles"] = profile_summary(directory)
    lines += [
        "",
        "## Profile attribution",
        "",
        (
            "Exclusive costs only. Native routines not visible separately remain in "
            "their enclosing Python function or other/native/dispatch. "
            "Profiled times are not uninstrumented latency."
        ),
        "",
        "| Case | Operation | Exclusive category | Share |",
        "| --- | --- | --- | ---: |",
    ]
    for row in derived["profiles"]:
        if row["operation"] in {
            "hash_first",
            "read_validated",
            "graph:shared:intermediate",
        }:
            for label, seconds in sorted(
                row["exclusive"].items(), key=operator.itemgetter(1), reverse=True
            )[:3]:
                lines.append(
                    f"| {row['case']} | "
                    f"{row['operation']} | "
                    f"{label} | "
                    f"{seconds / row['total']:.1%} |"
                )
    (directory / "summary.json").write_text(
        json.dumps(derived, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "measurements.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def profile_summary(directory: Path) -> list[dict]:
    """Return nonoverlapping profile categories alongside original call records."""
    invalid = invalid_cases(directory)
    results = []
    for path in sorted(directory.glob("*-profile-0.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        if batch["status"] != "passed" or batch["case"]["name"] in invalid:
            continue
        for row in batch["records"]:
            exclusive = defaultdict(float)
            for function in row["functions"]:
                exclusive[attribution(function)] += function["exclusive_seconds"]
            results.append(
                {
                    "case": batch["case"]["name"],
                    "operation": row["operation"],
                    "exclusive": dict(exclusive),
                    "total": sum(exclusive.values()),
                }
            )
    return results


def main() -> None:
    """Summarize an existing campaign without executing workloads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    render(parser.parse_args().directory)


if __name__ == "__main__":
    main()
