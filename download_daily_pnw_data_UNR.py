#!/usr/bin/env python3
"""
Download UNR .tenv3 GNSS time series for a station subset.

Typical workflow:
1) From your notebook, export a subset CSV (e.g., catalog_subset_bbox.csv).
2) Run this script on a server:
   python download_unr_tenv3.py --subset-csv outputs/catalog_subset_bbox.csv --out data/unr_tenv3

Outputs:
- downloads into OUT_DIR/<STATION>.tenv3
- outputs/manifest.csv (status per station)
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

import requests
from tqdm import tqdm


# ----------------------------
# Data model
# ----------------------------
@dataclass
class DownloadRecord:
    station: str
    url: str
    status: str  # downloaded | skipped_existing | not_found | failed
    http_status: Optional[int] = None
    bytes: Optional[int] = None
    path: Optional[str] = None
    error: Optional[str] = None


# ----------------------------
# IO helpers
# ----------------------------
def smoke_test(session: requests.Session, base_url: str, ext: str, product_suffix: str,  timeout_s: tuple[float, float]) -> None:
    test_station = "ALBH"
    url = build_station_url(base_url, test_station, ext, product_suffix)
    r = session.get(url, timeout=timeout_s, stream=True)
    r.raise_for_status()
    # pull a tiny bit to confirm body starts arriving
    next(r.iter_content(chunk_size=256), b"")


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
        if name_col not in reader.fieldnames:
            raise ValueError(f"CSV missing column '{name_col}'. Found: {reader.fieldnames}")
        names = []
        seen = set()
        for row in reader:
            s = str(row[name_col]).strip()
            if s and s not in seen:
                names.append(s)
                seen.add(s)
    return names


def build_station_url(base_url: str, station: str, ext: str, product_suffix: str = "") -> str:
    base_url = base_url.rstrip("/")
    station = station.strip()
    product_suffix = product_suffix.strip()

    # allow passing ".NA" or "NA"
    if product_suffix and not product_suffix.startswith("."):
        product_suffix = "." + product_suffix

    return f"{base_url}/{station}{product_suffix}{ext}"


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


# ----------------------------
# Download logic
# ----------------------------
def download_one(
        session: requests.Session,
        station: str,
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
            url=url,
            status="skipped_existing",
            http_status=None,
            bytes=out_path.stat().st_size,
            path=str(out_path),
        )

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout_s, stream=True)
            if r.status_code == 404:
                return DownloadRecord(station=station, url=url, status="not_found", http_status=404)

            r.raise_for_status()

            nbytes = atomic_write_bytes(out_path, r.iter_content(chunk_size=1024 * 256))
            return DownloadRecord(
                station=station,
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
                    f"{station}: attempt {attempt}/{retries} failed ({last_err}); retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
            else:
                break

    return DownloadRecord(station=station, url=url, status="failed", error=last_err)


def run_download(
        stations: list[str],
        base_url: str,
        out_dir: Path,
        ext: str,
        product_suffix:str,
        overwrite: bool,
        timeout_s: tuple[float, float],
        retries: int,
        backoff_s: float,
        user_agent: str,
        logger: logging.Logger
        ) -> list[DownloadRecord]:
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    logger.info(f"Base URL: {base_url}")
    smoke_test(session, base_url, ext, product_suffix, timeout_s)
    logger.info("Smoke test OK")

    records: list[DownloadRecord] = []
    for st in tqdm(stations, desc="Downloading", unit="station"):
        url = build_station_url(base_url, st, ext, product_suffix)
        suffix = product_suffix
        if suffix and not suffix.startswith("."):
            suffix = "." + suffix
        out_path = out_dir / f"{st}{suffix}{ext}"
        rec = download_one(
            session=session,
            station=st,
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
            logger.warning(f"{st}: {rec.status} ({rec.error or rec.http_status})")
        else:
            logger.info(f"{st}: {rec.status} ({rec.bytes} bytes)")

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

    # manifest.csv
    with manifest_csv.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(asdict(records[0]).keys()) if records else ["station", "url", "status"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(asdict(r))

    # manifest.json
    manifest_json.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")

    # lists
    not_found = [r.station for r in records if r.status == "not_found"]
    failed = [r.station for r in records if r.status == "failed"]
    not_found_txt.write_text("\n".join(not_found) + ("\n" if not_found else ""), encoding="utf-8")
    failed_txt.write_text("\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")

    # summary
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
    p = argparse.ArgumentParser(description="Download UNR GNSS .tenv3 data for a station subset.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--subset-csv", type=Path, help="CSV with a station name column (default column: name)")
    src.add_argument("--stations-txt", type=Path, help="TXT with one station name per line")

    p.add_argument("--name-col", type=str, default="name", help="Station name column in subset CSV (default: name)")

    p.add_argument("--base-url", type=str,
                   default="https://geodesy.unr.edu/gps_timeseries/IGS20/tenv3/IGS20",
                   help="UNR base URL for tenv3 data")
    p.add_argument("--ext", type=str, default=".tenv3", help="File extension (default: .tenv3)")
    p.add_argument("--product-suffix", type=str, default="",
                   help="Suffix inserted after station code in filename (e.g. '.NA' -> STATION.NA.tenv3)")

    p.add_argument("--out", type=Path, required=True, help="Directory to write downloaded files")
    p.add_argument("--outputs", type=Path, default=Path("./outputs"), help="Directory for manifest + lists")

    p.add_argument("--overwrite", action="store_true", help="Re-download even if file exists")
    p.add_argument("--retries", type=int, default=3, help="Number of retries per station (default: 3)")
    p.add_argument("--backoff", type=float, default=1.5, help="Backoff multiplier in seconds (default: 1.5)")
    p.add_argument("--connect-timeout", type=float, default=5.0, help="Connect timeout seconds (default: 5)")
    p.add_argument("--read-timeout", type=float, default=60.0, help="Read timeout seconds (default: 60)")

    p.add_argument("--log", type=Path, default=Path("./logs/unr_download.log"), help="Log file path")
    p.add_argument("--user-agent", type=str,
                   default="gnss-preprocessing/1.0 (UNR tenv3 downloader)",
                   help="User-Agent header")
    return p


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("unr_downloader")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if re-imported
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
        base_url=args.base_url,
        out_dir=args.out,
        ext=args.ext,
        product_suffix=args.product_suffix,
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
