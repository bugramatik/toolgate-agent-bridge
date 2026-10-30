from pathlib import Path

import pytest

from toolgate.manifest import ManifestError, load_manifests

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def test_loads_sample_manifests():
    specs = load_manifests(TOOLS_DIR)
    names = {spec.name for spec in specs}

    assert len(specs) == 12
    assert "incidents.list_open" in names
    assert "catalog.get_service" in names
    for spec in specs:
        assert spec.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert spec.url.startswith("http")
        assert spec.timeout_s > 0
        assert spec.retries >= 0


def test_rejects_missing_url(tmp_path: Path):
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        "tools:\n"
        "  - name: incidents.list_open\n"
        "    description: List open incidents.\n"
        "    method: GET\n"
        "    scopes: [incidents:read]\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError) as excinfo:
        load_manifests(tmp_path)

    assert "url" in str(excinfo.value)


def test_rejects_duplicate_names(tmp_path: Path):
    entry = (
        "  - name: incidents.list_open\n"
        "    description: List open incidents.\n"
        "    method: GET\n"
        "    url: https://incidents.example.com/v1/incidents\n"
        "    scopes: [incidents:read]\n"
    )
    (tmp_path / "a.yaml").write_text("tools:\n" + entry, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("tools:\n" + entry, encoding="utf-8")

    with pytest.raises(ManifestError) as excinfo:
        load_manifests(tmp_path)

    assert "duplicate" in str(excinfo.value).lower()


def test_rejects_unknown_scope_format(tmp_path: Path):
    bad = tmp_path / "scopes.yaml"
    bad.write_text(
        "tools:\n"
        "  - name: incidents.list_open\n"
        "    description: List open incidents.\n"
        "    method: GET\n"
        "    url: https://incidents.example.com/v1/incidents\n"
        "    scopes: [Incidents Read]\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError):
        load_manifests(tmp_path)
