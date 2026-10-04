"""Build contiguous conversational sequences from phrase-sized transcription bites."""

from __future__ import annotations

from typing import Any


MAX_SEQUENCE_GAP_SEC = 3.5
MAX_SEQUENCE_DURATION_SEC = 45.0


def group_story_bites(bites: list[dict[str, Any]], max_gap_sec: float = MAX_SEQUENCE_GAP_SEC, max_duration_sec: float = MAX_SEQUENCE_DURATION_SEC) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for bite in bites:
        by_source.setdefault(str(bite.get("source_path") or bite.get("filename") or ""), []).append(bite)
    sequence_number = 0
    for source, source_bites in by_source.items():
        ordered = sorted(source_bites, key=lambda item: (float(item.get("start_sec") or 0), float(item.get("end_sec") or 0)))
        current: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal current, sequence_number
            if not current:
                return
            # Never let a known truncated edge define a sequence boundary.
            while current and current[0].get("complete") is False:
                current.pop(0)
            while current and current[-1].get("complete") is False:
                current.pop()
            if not current:
                return
            sequence_number += 1
            start = float(current[0].get("start_sec") or 0)
            end = float(current[-1].get("end_sec") or start)
            grouped.append({
                "id": f"sequence-{sequence_number:04d}",
                "source_path": source,
                "filename": current[0].get("filename"),
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "bites": current[:],
                "text_original": " ".join(str(item.get("text_original") or item.get("text") or "").strip() for item in current).strip(),
                "complete": all(item.get("complete") is not False for item in current),
            })
            current = []

        for bite in ordered:
            if not current:
                current = [bite]
                continue
            previous_end = float(current[-1].get("end_sec") or 0)
            bite_start = float(bite.get("start_sec") or 0)
            current_start = float(current[0].get("start_sec") or 0)
            contiguous = bite_start - previous_end <= max_gap_sec
            within_limit = float(bite.get("end_sec") or bite_start) - current_start <= max_duration_sec
            if contiguous and within_limit:
                current.append(bite)
            else:
                flush()
                current = [bite]
        flush()
    return sorted(grouped, key=lambda item: (item["source_path"], item["start_sec"]))
