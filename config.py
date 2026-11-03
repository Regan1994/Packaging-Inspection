from pathlib import Path
import os
import sys


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

INSTANCE_DIR = Path(os.environ.get("INSPECTION_DATA_DIR", BASE_DIR / "instance"))
DATABASE_PATH = INSTANCE_DIR / "inspection.db"
BACKUP_DIR = INSTANCE_DIR / "backups"


class Config:
    APP_VERSION = "V701.11.03"
    SECRET_KEY = os.environ.get("INSPECTION_SECRET_KEY", "inspection-local-development-key")
    INITIAL_ACCESS_PASSWORD = "TJR"
    DATABASE = str(DATABASE_PATH)
    BACKUP_DIRECTORY = str(BACKUP_DIR)
    AUTO_BACKUP_ENABLED = True
    AUTO_BACKUP_RETENTION = 30
    # Bind on the local network so the printer and scanner PCs can use one database.
    HOST = os.environ.get("INSPECTION_HOST", "0.0.0.0")
    BROWSER_HOST = os.environ.get("INSPECTION_BROWSER_HOST", "127.0.0.1")
    PORT = int(os.environ.get("INSPECTION_PORT", "5000"))

