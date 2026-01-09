# GNSS Daily Time Series – Preprocessing & NetCDF Build

This repository provides a **clean, reproducible pipeline** to download, inspect, and package daily GNSS position time series into analysis‑ready **NetCDF/xarray** datasets.

The goal is to produce **one NetCDF per data provider** (UNR, PANGA, SOPAC) with a **shared schema**, making downstream analysis, comparison, and visualization straightforward.

---

## What this repo does

- Download daily GNSS time series (currently UNR `tenv3`)
- Track download status with a manifest (`downloaded`, `skipped_existing`, `not_found`, `failed`)
- Explore and QA the data in Jupyter notebooks
- Filter stations and time ranges for scientific use
- Export **provider‑specific NetCDF files** optimized for xarray

---

## Data products

Final outputs are **NetCDF files**, one per provider:

```
outputs/
├── gnss_unr_2010_present_5y.nc
├── gnss_panga_2010_present_5y.nc   (planned)
└── gnss_sopac_2010_present_5y.nc   (planned)
```

### Common dataset layout

**Dimensions**
- `time` – daily `datetime64[ns]` (2010‑01‑01 → 2025‑12‑31)
- `station` – GNSS station code

**Station coordinates**
- `lat` (deg)
- `lon` (deg)
- `elev_m` (m)
- `depth_m` (m)

**Data variables** (meters)
- `east_m`, `north_m`, `up_m`
- `east_sigma_m`, `north_sigma_m`, `up_sigma_m`

All providers share the same schema.

---

## Scientific filtering rules

Applied consistently across providers:

- **Time window:** 2010‑01‑01 → 2025‑12‑31 (inclusive)
- **Minimum data span:** ≥ 5 years  
  (campaign sites are removed)
- **Cadence:** daily
- **Units:** meters (precision ~1e‑4)

---

## Repository structure

```
.
├── download_unr_tenv3.py
├── data/
│   └── unr_tenv3/
├── outputs/
│   ├── manifest.csv
│   ├── catalog_subset_bbox.csv
│   └── gnss_unr_2010_present_5y.nc
├── notebooks/
│   ├── gnss_station_eda.ipynb
│   └── gnss_unr_eda_xarray.ipynb
└── README.md
```

---

## Quick start

### 1) Download UNR data

```bash
python download_unr_tenv3.py   --subset-csv outputs/catalog_subset_bbox.csv   --out data/unr_tenv3   --outputs outputs   --limit 20
```

Remove `--limit` for the full run.

---

### 2) Explore and QA

Open the EDA notebook:

```bash
jupyter notebook notebooks/gnss_unr_eda_xarray.ipynb
```

This notebook:
- summarizes the manifest
- plots sample station time series with uncertainties
- builds the UNR NetCDF using xarray

---

## Design notes

- Station metadata (lat/lon/elev/depth) comes from the station catalog, not the time series files.
- Equipment changes are intentionally excluded and handled via a separate jump table.
- NetCDF files are losslessly compressed for efficient storage and I/O.
- Time is stored as **daily datetime**, not decimal year.

---

## Data sources

- UNR: https://geodesy.unr.edu/gps_timeseries/
- PANGA: https://www.geodesy.org/panga/officialresults/archives/panga_cleaned.zip
- SOPAC: http://garner.ucsd.edu/pub/measuresESESES_products/Timeseries/WesternNorthAmerica/

---

## Status

- ✅ UNR pipeline complete
- 🔧 PANGA parser planned
- 🔧 SOPAC parser planned

