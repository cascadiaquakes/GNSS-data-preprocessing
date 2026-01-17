#!/usr/bin/env python3
"""
Download PANGA GNSS time series for a station subset via the CGI endpoint.

Example URL format:
https://www.panga.org/cgi-bin/timeseries_data.pl?n=panga&s=ALBH&p=cleaned&f=daily&c=lat

This script downloads multiple components per station, e.g. lat/lon/rad.

Outputs:
- downloads into OUT_DIR/<STATION>.<COMP>.txt  (or .dat; configurable)
- outputs/manifest.csv (status per station+component)
- outputs/not_found.txt and outputs/failed.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlencode

import requests
from tqdm import tqdm


# ----------------------------
# Data model
# ----------------------------
@dataclass
class DownloadRecord:
    station: str
    component: str
    url: str
    status: str  # downloaded | skipped_existing | not_found | failed
    http_status: Optional[int] = None
    bytes: Optional[int] = None
    path: Optional[str] = None
    error: Optional[str] = None


# ----------------------------
# IO helpers
# ----------------------------
def read_station_list_txt(path: Path) -> list[str]:
    names: list[str] = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s not in seen:
            names.append(s)
            seen.add(s)
    return names


def read_station_names_from_subset_csv(path: Path, name_col: str = "name") -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or name_col not in reader.fieldnames:
            raise ValueError(f"CSV missing column '{name_col}'. Found: {reader.fieldnames}")
        names: list[str] = []
        seen = set()
        for row in reader:
            s = str(row.get(name_col, "")).strip()
            if s and s not in seen:
                names.append(s)
                seen.add(s)
    return names


def build_panga_url(
    base_url: str,
    station: str,
    component: str,
    network: str,
    product: str,
    freq: str,
) -> str:
    """
    Builds:
    {base_url}?n=panga&s=ALBH&p=cleaned&f=daily&c=lat
    """
    base_url = base_url.rstrip("?")
    qs = urlencode(
        {
            "n": network,
            "s": station.strip(),
            "p": product,
            "f": freq,
            "c": component.strip(),
        }
    )
    return f"{base_url}?{qs}"


def looks_like_valid_timeseries(body_head: str) -> bool:
    return body_head.lstrip().startswith("# ================= DQRFIT RESULTS")

def is_probably_no_data(body_head: str) -> bool:
    t = body_head.lower()
    needles = [
        "unknown station",
        "station not found",
        "no such station",
        "no matching data",
        "no data available",
    ]
    return any(n in t for n in needles)


def atomic_write_bytes(dst: Path, content_iter: Iterable[bytes]) -> int:
    tmp = dst.with_suffix(dst.suffix + ".part")
    nbytes = 0
    with tmp.open("wb") as f:
        for chunk in content_iter:
            if chunk:
                f.write(chunk)
                nbytes += len(chunk)
    tmp.replace(dst)
    return nbytes


def smoke_test(
    session: requests.Session,
    base_url: str,
    timeout_s: tuple[float, float],
    network: str,
    product: str,
    freq: str,
    component: str,
    test_station: str = "ALBH",
) -> None:
    url = build_panga_url(
        base_url=base_url,
        station=test_station,
        component=component,
        network=network,
        product=product,
        freq=freq,
    )

    # Don't stream for smoke test; just fetch a small response reliably.
    r = session.get(url, timeout=timeout_s)
    r.raise_for_status()

    head = (r.text or "")[:2048].strip()
    if not head:
        raise RuntimeError(f"Smoke test got empty body for {test_station}/{component}.")

    # Optional: only reject if it's *clearly* a no-data message.
    if is_probably_no_data(head) and len(head) < 5000:
        raise RuntimeError(
            f"Smoke test looks like no-data for {test_station}/{component}. "
            f"First 200 chars: {head[:200]!r}"
        )


# ----------------------------
# Download logic
# ----------------------------
def download_one(
    session: requests.Session,
    station: str,
    component: str,
    url: str,
    out_path: Path,
    overwrite: bool,
    timeout_s: tuple[float, float],
    retries: int,
    backoff_s: float,
    logger: logging.Logger,
) -> DownloadRecord:
    if out_path.exists() and not overwrite:
        return DownloadRecord(
            station=station,
            component=component,
            url=url,
            status="skipped_existing",
            http_status=None,
            bytes=out_path.stat().st_size,
            path=str(out_path),
        )

    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout_s, stream=True)

            # Some servers will genuinely 404
            if r.status_code == 404:
                return DownloadRecord(station=station, component=component, url=url, status="not_found", http_status=404)

            r.raise_for_status()

            # Sniff the first couple KB to detect "no data" returned with 200
            head_bytes = b""
            it = r.iter_content(chunk_size=1024 * 64)
            try:
                head_bytes = next(it, b"")
            except Exception:
                head_bytes = b""

            head_text = ""
            try:
                head_text = head_bytes.decode("utf-8", errors="replace")[:2048]
            except Exception:
                head_text = ""

            if head_text and is_probably_no_data(head_text) and len(head_bytes) < 2048:
                return DownloadRecord(
                    station=station,
                    component=component,
                    url=url,
                    status="not_found",
                    http_status=r.status_code,
                    error=head_text.strip()[:300],
                )

            # Write head + remaining stream atomically
            def content_iter():
                if head_bytes:
                    yield head_bytes
                for chunk in it:
                    yield chunk

            nbytes = atomic_write_bytes(out_path, content_iter())
            return DownloadRecord(
                station=station,
                component=component,
                url=url,
                status="downloaded",
                http_status=r.status_code,
                bytes=nbytes,
                path=str(out_path),
            )

        except Exception as e:
            last_err = repr(e)
            if attempt < retries:
                sleep_s = backoff_s * attempt
                logger.warning(
                    f"{station}/{component}: attempt {attempt}/{retries} failed ({last_err}); retrying in {sleep_s:.1f}s"
                )
                time.sleep(sleep_s)
            else:
                break

    return DownloadRecord(station=station, component=component, url=url, status="failed", error=last_err)


def run_download(
    stations: list[str],
    components: list[str],
    base_url: str,
    out_dir: Path,
    out_ext: str,
    network: str,
    product: str,
    freq: str,
    overwrite: bool,
    timeout_s: tuple[float, float],
    retries: int,
    backoff_s: float,
    user_agent: str,
    logger: logging.Logger,
) -> list[DownloadRecord]:
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    logger.info(f"Base URL: {base_url}")
    logger.info(f"Params: n={network} p={product} f={freq} components={components}")

    # smoke test on first component
    smoke_test(
        session=session,
        base_url=base_url,
        timeout_s=timeout_s,
        network=network,
        product=product,
        freq=freq,
        component=components[0],
    )
    logger.info("Smoke test OK")

    records: list[DownloadRecord] = []
    total = len(stations) * len(components)

    with tqdm(total=total, desc="Downloading", unit="file") as pbar:
        for st in stations:
            for comp in components:
                url = build_panga_url(
                    base_url=base_url,
                    station=st,
                    component=comp,
                    network=network,
                    product=product,
                    freq=freq,
                )

                logger.info(f"Requesting {st}/{comp}: {url}")

                out_path = out_dir / f"{st}.{comp}{out_ext}"

                rec = download_one(
                    session=session,
                    station=st,
                    component=comp,
                    url=url,
                    out_path=out_path,
                    overwrite=overwrite,
                    timeout_s=timeout_s,
                    retries=retries,
                    backoff_s=backoff_s,
                    logger=logger,
                )
                records.append(rec)

                if rec.status in ("failed", "not_found"):
                    logger.warning(f"{st}/{comp}: {rec.status} ({rec.error or rec.http_status})")
                else:
                    logger.info(f"{st}/{comp}: {rec.status} ({rec.bytes} bytes)")

                pbar.update(1)

    return records


# ----------------------------
# Reporting
# ----------------------------
def write_outputs(records: list[DownloadRecord], out_dir: Path, logger: logging.Logger) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_csv = out_dir / "manifest.csv"
    manifest_json = out_dir / "manifest.json"
    not_found_txt = out_dir / "not_found.txt"
    failed_txt = out_dir / "failed.txt"

    with manifest_csv.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(asdict(records[0]).keys()) if records else ["station", "component", "url", "status"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(asdict(r))

    manifest_json.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")

    not_found = [f"{r.station}.{r.component}" for r in records if r.status == "not_found"]
    failed = [f"{r.station}.{r.component}" for r in records if r.status == "failed"]
    not_found_txt.write_text("\n".join(not_found) + ("\n" if not_found else ""), encoding="utf-8")
    failed_txt.write_text("\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")

    from collections import Counter
    counts = Counter(r.status for r in records)
    logger.info(f"Summary: {dict(counts)}")
    logger.info(f"Wrote: {manifest_csv}")
    logger.info(f"Wrote: {manifest_json}")
    logger.info(f"Wrote: {not_found_txt}")
    logger.info(f"Wrote: {failed_txt}")


# ----------------------------
# CLI
# ----------------------------
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download PANGA GNSS time series for a station subset.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--subset-csv", type=Path, help="CSV with a station name column (default column: name)")
    src.add_argument("--stations-txt", type=Path, help="TXT with one station name per line")

    p.add_argument("--name-col", type=str, default="name", help="Station name column in subset CSV (default: name)")

    p.add_argument(
        "--base-url",
        type=str,
        default="https://www.panga.org/cgi-bin/timeseries_data.pl",
        help="PANGA CGI endpoint base URL",
    )
    p.add_argument("--network", type=str, default="panga", help="n= parameter (default: panga)")
    p.add_argument("--product", type=str, default="cleaned", help="p= parameter (default: cleaned)")
    p.add_argument("--freq", type=str, default="daily", help="f= parameter (default: daily)")

    p.add_argument(
        "--components",
        type=str,
        default="lat,lon,rad",
        help="Comma-separated components for c= (default: lat,lon,rad)",
    )

    p.add_argument("--out", type=Path, required=True, help="Directory to write downloaded files")
    p.add_argument("--out-ext", type=str, default=".txt", help="Output file extension (default: .txt)")
    p.add_argument("--outputs", type=Path, default=Path("./outputs"), help="Directory for manifest + lists")

    p.add_argument("--overwrite", action="store_true", help="Re-download even if file exists")
    p.add_argument("--retries", type=int, default=3, help="Number of retries per file (default: 3)")
    p.add_argument("--backoff", type=float, default=1.5, help="Backoff multiplier in seconds (default: 1.5)")
    p.add_argument("--connect-timeout", type=float, default=5.0, help="Connect timeout seconds (default: 5)")
    p.add_argument("--read-timeout", type=float, default=60.0, help="Read timeout seconds (default: 60)")

    p.add_argument("--log", type=Path, default=Path("./logs/panga_download.log"), help="Log file path")
    p.add_argument(
        "--user-agent",
        type=str,
        default="gnss-preprocessing/1.0 (PANGA timeseries downloader)",
        help="User-Agent header",
    )
    return p


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("panga_downloader")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")

        fh = logging.FileHandler(log_path)
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)

        logger.addHandler(fh)
        logger.addHandler(sh)

    return logger


def main() -> int:
    args = build_argparser().parse_args()
    logger = setup_logger(args.log)

    components = [c.strip() for c in args.components.split(",") if c.strip()]
    if not components:
        logger.error("No components specified. Exiting.")
        return 2

    if args.subset_csv:
        stations = read_station_names_from_subset_csv(args.subset_csv, name_col=args.name_col)
        logger.info(f"Loaded {len(stations)} stations from subset CSV: {args.subset_csv}")
    else:
        stations = read_station_list_txt(args.stations_txt)
        logger.info(f"Loaded {len(stations)} stations from TXT: {args.stations_txt}")

    if not stations:
        logger.error("No stations found. Exiting.")
        return 2

    records = run_download(
        stations=stations,
        components=components,
        base_url=args.base_url,
        out_dir=args.out,
        out_ext=args.out_ext,
        network=args.network,
        product=args.product,
        freq=args.freq,
        overwrite=args.overwrite,
        timeout_s=(args.connect_timeout, args.read_timeout),
        retries=args.retries,
        backoff_s=args.backoff,
        user_agent=args.user_agent,
        logger=logger,
    )

    write_outputs(records, args.outputs, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())