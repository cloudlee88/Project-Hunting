import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_env_values(raw: str) -> List[str]:
    values: List[str] = []
    for part in (raw or "").replace("\n", ",").split(","):
        value = part.strip().strip('"').strip("'")
        if value:
            values.append(value)
    return values


def _read_repeated_env_values(path: Path, key: str) -> List[str]:
    values: List[str] = []
    if not path.exists():
        return values
    prefix = f"{key}="
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or not line.startswith(prefix):
                continue
            values.extend(_split_env_values(line.split("=", 1)[1]))
    except OSError:
        return values
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Affiliate Hub"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    session_cookie_name: str = "ah_session"
    session_ttl_hours: int = 720
    cors_origins_raw: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3001"
    data_dir: Path = Path("./data")
    cloak_browser_path: str = ""
    headless: bool = True
    capsolver_api_key: str = ""
    capsolver_app_id: str = ""
    # SimilarWeb traffic scan (port từ api-adecos)
    similarweb_email: str = ""
    similarweb_password: str = ""
    selenium_hub_url: str = ""
    novnc_url: str = "http://localhost:7900"

    # LLM cho browser-use auto-signup (Gemini ưu tiên)
    gemini_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    signup_llm_model: str = "gemini-3.5-flash"
    signup_max_steps: int = 60
    signup_worker_concurrency: int = 2  # số job signup chạy song song
    signup_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # SMS OTP receive service (smspool.net mặc định; fallback 5sim | sms-activate)
    sms_otp_provider: str = "smspool"  # smspool | 5sim | sms-activate
    sms_otp_api_key: str = ""
    # SMSPool dùng ID dạng số: country (vd 241=VN, 1=US), product/service (vd 823=Shopee, 1=Any)
    sms_otp_country: str = "1"
    sms_otp_operator: str = "any"
    sms_otp_product: str = "1"
    sms_otp_timeout_sec: int = 180

    # Email verification (IMAP). Có thể override per-profile qua profile['imap'].
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str = ""
    imap_password: str = ""
    imap_timeout_sec: int = 180

    # SerpAPI (Google Ads Transparency Center)
    serpapi_keys: str = ""

    def _get_api_keys_list(self, env_var: str, fallback: str = "") -> List[str]:
        env_file = Path(str(self.model_config.get("env_file") or ".env"))
        fresh_values: List[str] = []
        fresh_values.extend(_read_repeated_env_values(env_file, env_var))
        fresh_values.extend(_split_env_values(os.getenv(env_var, "")))
        values = list(fresh_values)
        if not fresh_values and fallback:
            values.extend(_split_env_values(fallback))
        deduped: List[str] = []
        seen: set[str] = set()
        for v in values:
            if v not in seen:
                deduped.append(v)
                seen.add(v)
        return deduped

    @property
    def cors_origins(self) -> List[str]:
        return [s.strip() for s in self.cors_origins_raw.split(",") if s.strip()]

    @property
    def serpapi_keys_list(self) -> List[str]:
        return [k.strip() for k in self.serpapi_keys.split(",") if k.strip()]

    @property
    def gemini_api_keys_list(self) -> List[str]:
        return self._get_api_keys_list("GEMINI_API_KEY", self.gemini_api_key)

    @property
    def openai_api_keys_list(self) -> List[str]:
        return self._get_api_keys_list("OPENAI_API_KEY", self.openai_api_key)

    @property
    def deepseek_api_keys_list(self) -> List[str]:
        return self._get_api_keys_list("DEEPSEEK_API_KEY", self.deepseek_api_key)

    def data_path(self, *parts: str) -> Path:
        return self.data_dir.joinpath(*parts)


settings = Settings()


def read_env_value(key: str) -> str:
    """Đọc 1 key từ file .env lúc runtime — pick up thay đổi không cần restart."""
    env_file = Path(str(settings.model_config.get("env_file") or ".env"))
    path = env_file if env_file.is_absolute() else Path.cwd() / env_file
    prefix = f"{key}="
    if not path.exists():
        return ""
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(prefix):
                return line[len(prefix):].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""
