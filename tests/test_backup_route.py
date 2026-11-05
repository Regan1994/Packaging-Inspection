from pathlib import Path


def test_backup_route_accepts_get_for_legacy_home_button(client, app):
    response = client.get("/backup")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))
