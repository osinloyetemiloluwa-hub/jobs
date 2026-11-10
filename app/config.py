from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    e2b_api_key: str
    runner_api_keys: str
    tick_secret: str
    tick_batch_size: int = 3
    public_base_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()