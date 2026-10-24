"""The retrieval set's gold chunks, exported for a person to review and imported back.

    uv run python -m evals.retrieval_review export   # writes evals/retrieval_review.csv
    uv run python -m evals.retrieval_review import   # gold becomes the rows marked yes

One row per question and proposed chunk, with the chunk's whole text. The reviewer writes `yes` or
`no` in `keep`, and may add a row with the id of a chunk the proposal missed. Import refuses a
blank `keep`, so the set only counts as reviewed once every row was read.
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from app.rag.chunk import Chunk, chunk_documents
from app.rag.ingest import read_snapshot

QUERIES = Path(__file__).parent / "retrieval_calibration.jsonl"
REVIEW = Path(__file__).parent / "retrieval_review.csv"
FIELDS = ["id", "query", "chunk_id", "source", "text", "keep", "note"]


def read_set(path: Path = QUERIES) -> list[dict]:
    """The retrieval set, in file order."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def corpus_chunks() -> dict[str, Chunk]:
    """The chunks of the committed snapshot, by id: the ids the gold refers to."""
    chunks, _ = chunk_documents(read_snapshot())
    return {chunk.id: chunk for chunk in chunks}


def export(rows: list[dict], chunks: dict[str, Chunk], path: Path = REVIEW) -> int:
    """Write one blank-verdict row per proposed gold chunk and return how many."""
    written = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        for row in rows:
            for chunk_id in row.get("gold", []):
                chunk = chunks[chunk_id]
                source = f"{chunk.title} | {chunk.url}"
                writer.writerow(
                    {**dict.fromkeys(FIELDS, ""), "id": row["id"], "query": row["query"]}
                    | {"chunk_id": chunk_id, "source": source, "text": chunk.text}
                )
                written += 1
    return written


def read_review(path: Path = REVIEW) -> list[dict]:
    """The review rows as the reviewer left them."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def problems(review: list[dict], rows: list[dict], chunks: dict[str, Chunk]) -> list[str]:
    """Why a review cannot be imported yet; empty when it can."""
    in_domain = {row["id"] for row in rows if row["group"] == "in_domain"}
    found = [
        f"{line['id']} {line['chunk_id']}: keep must be yes or no"
        for line in review
        if line["keep"].strip().lower() not in ("yes", "no")
    ]
    found += [
        f"{line['id']}: not an in-domain question" for line in review if line["id"] not in in_domain
    ]
    found += [
        f"{line['chunk_id']}: no such chunk" for line in review if line["chunk_id"] not in chunks
    ]
    kept = {line["id"] for line in review if line["keep"].strip().lower() == "yes"}
    found += [
        f"{qid}: no chunk kept; add one, or move it out of the domain"
        for qid in sorted(in_domain - kept)
    ]
    return found


def apply_review(review: list[dict], rows: list[dict]) -> list[dict]:
    """The set with each in-domain question's gold replaced by the chunks kept for it."""
    kept: dict[str, list[str]] = defaultdict(list)
    for line in review:
        if line["keep"].strip().lower() == "yes" and line["chunk_id"] not in kept[line["id"]]:
            kept[line["id"]].append(line["chunk_id"])
    return [
        {**row, "gold": kept[row["id"]]} if row["group"] == "in_domain" else row for row in rows
    ]


def is_reviewed(rows: list[dict], path: Path = REVIEW) -> bool:
    """Whether every proposed chunk was given a verdict and the set holds what was kept."""
    if not path.is_file():
        return False
    review = read_review(path)
    if any(line["keep"].strip().lower() not in ("yes", "no") for line in review):
        return False
    return apply_review(review, rows) == rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["export", "import"])
    action = parser.parse_args().action
    rows, chunks = read_set(), corpus_chunks()
    if action == "export":
        print(f"{export(rows, chunks)} rows written to {REVIEW}")
        return
    review = read_review()
    if found := problems(review, rows, chunks):
        raise SystemExit("review not imported:\n" + "\n".join(found))
    reviewed = apply_review(review, rows)
    QUERIES.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reviewed), encoding="utf-8"
    )
    print(f"gold of {sum(row['group'] == 'in_domain' for row in reviewed)} questions imported")


if __name__ == "__main__":
    main()
