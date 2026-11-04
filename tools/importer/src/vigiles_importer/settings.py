from pathlib import Path

from vigiles_common.config import ServiceSettings


class ImporterSettings(ServiceSettings):
    service_name: str = "importer"
    importer_database_url: str
    importer_data_dir: Path = Path("data")
    importer_poll_interval_s: float = 10.0
    # A file younger than this is assumed to be still being copied.
    importer_settle_s: float = 2.0
