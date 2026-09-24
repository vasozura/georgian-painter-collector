from pathlib import Path
import io
import zipfile

from PIL import Image

from src.collector import (
    average_hash,
    image_signature_ok,
    license_ok,
    normalized_key,
    safe_name,
    validate_zip,
)


def make_jpeg(seed: int = 1) -> bytes:
    img = Image.new("RGB", (64, 48), (seed % 255, (seed * 3) % 255, (seed * 7) % 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_safe_name():
    assert safe_name('a:b/c*?') == 'a_b_c__'


def test_license_public_domain():
    ok, name, _ = license_ok({"LicenseShortName": {"value": "Public domain"}})
    assert ok
    assert name == "Public domain"


def test_license_reject_nc():
    ok, _, _ = license_ok({"LicenseShortName": {"value": "CC BY-NC 4.0"}})
    assert not ok


def test_image_validation_and_hash():
    data = make_jpeg()
    ok, w, h, fmt = image_signature_ok(data, "image/jpeg")
    assert ok
    assert (w, h, fmt) == (64, 48, "JPEG")
    assert isinstance(average_hash(data), int)


def test_normalized_artwork_key_removes_copy_words():
    assert normalized_key("My Painting - cropped copy") == normalized_key("My Painting")


def test_zip_validation(tmp_path: Path):
    zpath = tmp_path / "ok.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("SOURCES.txt", "ok")
        for i in range(8):
            zf.writestr(f"Works/{i}.jpg", make_jpeg(i + 20))
    validate_zip(zpath, 8)
