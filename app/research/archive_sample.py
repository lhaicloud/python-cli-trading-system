"""Memory-bounded sampling of checksum-verified Binance Vision CSV archives."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile

import httpx

from app.research.binance_archive import ArchiveError, VerifiedArchive, _parse_checksum


def download_verified_archive_sample(
    url: str,
    *,
    max_rows: int = 10_000,
    max_compressed_bytes: int = 256 * 1024 * 1024,
    client: httpx.Client | None = None,
) -> VerifiedArchive:
    """Verify the complete ZIP checksum but decompress only a bounded CSV sample.

    This is intended for extremely large event-level archives such as
    `bookTicker`. Integrity is checked against Binance's SHA-256 over the whole
    downloaded ZIP; the CSV member is then streamed row-by-row and only the
    first `max_rows` data rows are retained in memory.
    """
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    if max_compressed_bytes <= 0:
        raise ValueError("max_compressed_bytes must be positive")

    owns = client is None
    http = client or httpx.Client(timeout=90.0, follow_redirects=True)
    try:
        checksum_response = http.get(url + ".CHECKSUM")
        checksum_response.raise_for_status()
        expected_filename = url.rsplit("/", 1)[-1]
        expected_sha = _parse_checksum(checksum_response.text, expected_filename)

        response = http.get(url)
        response.raise_for_status()
        payload = response.content
        if len(payload) > max_compressed_bytes:
            raise ArchiveError(
                f"compressed archive exceeds sample limit: {len(payload)} > {max_compressed_bytes}"
            )
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise ArchiveError(f"checksum mismatch for {expected_filename}")

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                members = [info for info in zf.infolist() if not info.is_dir()]
                csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
                if len(csv_members) != 1:
                    raise ArchiveError(f"expected one CSV member, got {len(csv_members)}")
                info = csv_members[0]
                normalized = info.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise ArchiveError("unsafe archive member path")

                with zf.open(info, "r") as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                    reader = csv.reader(text)
                    first: tuple[str, ...] | None = None
                    for row in reader:
                        if row:
                            first = tuple(cell.strip() for cell in row)
                            break
                    if first is None:
                        raise ArchiveError("archive CSV is empty")
                    try:
                        float(first[0])
                        header: tuple[str, ...] | None = None
                        rows: list[tuple[str, ...]] = [first]
                    except (TypeError, ValueError):
                        header = first
                        rows = []

                    for row in reader:
                        if not row:
                            continue
                        rows.append(tuple(cell.strip() for cell in row))
                        if len(rows) >= max_rows:
                            break
                    if not rows:
                        raise ArchiveError("archive CSV has header but no data rows")
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise ArchiveError("invalid ZIP/CSV archive") from exc

        return VerifiedArchive(
            url=url,
            sha256=actual_sha,
            member_name=info.filename,
            header=header,
            rows=tuple(rows),
        )
    except httpx.HTTPError as exc:
        raise ArchiveError(f"archive request failed: {exc}") from exc
    finally:
        if owns:
            http.close()
