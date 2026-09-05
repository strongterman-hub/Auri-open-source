from pathlib import Path

from app.services.update_service import AppUpdateService


def test_update_apk_uses_immutable_sha256_url(tmp_path: Path) -> None:
    service = AppUpdateService(tmp_path)
    release = service.save_apk(b"apk-v1")

    assert release.download_url == f"/update/apk/{release.sha256}"
    assert service.apk_path_for_sha256(release.sha256 or "") is not None

    old_sha256 = release.sha256 or ""
    service.save_apk(b"apk-v2")
    old_path = service.apk_path_for_sha256(old_sha256)
    assert old_path is not None
    assert old_path.read_bytes() == b"apk-v1"


def test_existing_fixed_url_is_exposed_as_versioned_url(tmp_path: Path) -> None:
    service = AppUpdateService(tmp_path)
    release = service.save_apk(b"current-apk")
    release.download_url = "/update/apk"
    service._write(release)

    public = service.get().public_dict()
    assert public["download_url"] == f"/update/apk/{release.sha256}"
    assert service.apk_path_for_sha256("not-a-sha") is None
