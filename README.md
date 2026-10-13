# GNSS Daily Time Series Preprocessing

[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](LICENSE)

This repository builds analysis-ready GNSS time-series datasets from multiple providers and exports them as NetCDF files for `xarray`.

The common dataset structure is:

- dimensions: `time`, `station`
- station coordinates: `lat`, `lon`
- displacement variables in meters: `east_m`, `north_m`, `up_m`
- uncertainty variables in meters: `east_sigma_m`, `north_sigma_m`, `up_sigma_m`

**Processed datasets:** https://zenodo.org/records/20276956

---

## Repository Layout

```text
.
|-- data/
|-- logs/
|-- outputs/
|-- resources/
|-- download_daily_pnw_data_UNR.py
|-- download_daily_pnw_data_panga.py
|-- gnss_unr_eda_xarray.ipynb
|-- gnss_panga_eda_xarray.ipynb
|-- gnss_SOPAC_eda_xarray.ipynb
|-- quick_start_guide.ipynb
`-- README.md
```

## Workflows

### UNR

Use the download script first, then build and inspect the dataset in the notebook.

1. Download the raw UNR files:

```bash
python download_daily_pnw_data_UNR.py
```

2. Open:

- `gnss_unr_eda_xarray.ipynb`

This workflow uses the downloaded UNR `tenv3` files and builds the UNR NetCDF outputs.

### PANGA

PANGA is a direct download workflow. Download the source files from:

- https://www.panga.org/

Then open:

- `gnss_panga_eda_xarray.ipynb`

The current PANGA notebook targets `data/panga_cleaned_v2/`, where each station is stored as:

- `STATION.lon`
- `STATION.lat`
- `STATION.rad`

Each component file contains decimal year, residual displacement, and sigma. The notebook:

- maps `.lon`, `.lat`, `.rad` to `east`, `north`, `up`
- merges components by `dec_year`
- builds the time index from decimal year
- rounds to the nearest day after converting with the actual calendar-year length
- filters stations using `resources/catalog_subset_bbox.csv`
- exports `outputs/gnss_PANGA_2010_2025_5y_NA_v2.nc`

### SOPAC

SOPAC is also a direct download workflow. Download the source files from:

- https://garner.ucsd.edu/pub/measuresESESES_products/Timeseries/WesternNorthAmerica/

Then open:

- `gnss_SOPAC_eda_xarray.ipynb`

## Dependencies

Install the Python dependencies with:

```bash
pip install -r requirements.txt
```

The notebooks expect a Python environment with:

- `pandas`
- `numpy`
- `xarray`
- `matplotlib`
- Jupyter support

## Processed datasets and guide

Processed datasets are published on Zenodo:

- https://zenodo.org/records/20276956

For a quick tutorial on how to open and use the processed data directly from zenodo, have a look at this notebook:

- `quick_start_guide.ipynb`

---

## License

This project is licensed under the BSD 3-Clause License. See [LICENSE](LICENSE).

---

## Contacts

- **Loïc Bachelot**, University of Oregon: [lbachelo@uoregon.edu](mailto:lbachelo@uoregon.edu)

For bugs or questions, please [open an issue](https://github.com/cascadiaquakes/GNSS-data-preprocessing/issues).

---

## Acknowledgments

<table>
  <tr>
    <td align="center" valign="middle">
      <img src="https://raw.githubusercontent.com/cascadiaquakes/.github/main/assets/crescent-circle.png" alt="CRESCENT Logo" width="150" />
    </td>
    <td align="center" valign="middle">
      <img src="https://raw.githubusercontent.com/cascadiaquakes/.github/main/assets/nsf-logo.png" alt="NSF Logo" width="450" />
    </td>
  </tr>
</table>

This project is supported by the National Science Foundation through the Cascadia Region Earthquake Science Center (CRESCENT), Cooperative Agreement EAR-2225286.
