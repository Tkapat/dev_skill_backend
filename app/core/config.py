from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongodb_uri: str
    mongodb_db: str
    jwt_secret: str
    fernet_key: str
    edge_signing_key_hex: str
    file_url_secret: str
    device_id: str = "edge-001"
    allowed_origins: str = "http://localhost:3000,http://localhost:3001"
    local_db_path: str = "data/edge.db"
    evidence_dir: str = "data/evidence"
    force_offline: bool = False
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()