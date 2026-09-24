# Georgian Painter Collector

Hourly GitHub Actions collector for rights-clear images of Georgian painters.

What it does on each successful run:

1. Chooses the first painter not already recorded in `state/history.json`.
2. Queries Wikimedia Commons for curated painter/category files.
3. Reads machine-readable Commons image metadata (`imageinfo` + `extmetadata`).
4. Accepts Public Domain, CC0, CC BY and CC BY-SA material; rejects NC/ND and unknown licenses.
5. Downloads the actual JPEG/PNG binary and verifies that Pillow can decode it.
6. Deduplicates by artwork key, SHA-256, and a conservative perceptual hash check.
7. Requires at least 8 successfully downloaded images; otherwise it tries the next painter during the same run.
8. Creates one ZIP containing the image files, `SOURCES.txt`, and `manifest.json`.
9. Uploads the ZIP as a GitHub Actions artifact for 30 days.
10. Only after ZIP validation succeeds, records the painter in `state/history.json` and commits that state back to the repository.

## Install on GitHub

No API key or secret is required for Wikimedia Commons.

The workflow runs once per hour at minute 17 in the `Asia/Tbilisi` timezone and can also be started manually from **Actions → Georgian Painter Collector → Run workflow**.

The workflow needs `contents: write` so it can commit `state/history.json`. If your repository settings restrict the default `GITHUB_TOKEN`, open **Settings → Actions → General → Workflow permissions** and allow read/write permissions.

## Download an archive

Open **Actions**, select a completed run, and download the artifact at the bottom of the run page. The artifact itself is a ZIP containing the painter ZIP; unzip the GitHub artifact once, then open the painter archive inside it.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
python src/collector.py
```

## Configuration

Edit `config/painters.json` to add painters or change:

- `min_images`: minimum actual downloadable images required for success.
- `max_images`: maximum images per painter ZIP.
- categories and defensible series folders.
- artist aliases used to exclude unrelated photos/stamps from mixed Commons categories.

For a painter whose Commons category is explicitly a works-only category, set `require_artist_match` to `false`. For a mixed category, keep it `true` and provide aliases.

## Rights handling

The collector relies on Wikimedia Commons machine-readable license metadata at download time. `SOURCES.txt` preserves the source page, license name/URL, original filename, downloaded filename, dimensions, credit metadata, and SHA-256 for every included image. Review `SOURCES.txt` before external redistribution if you need a legal/commercial publication workflow.
