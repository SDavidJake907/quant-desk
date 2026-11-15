"""Runtime configuration, loaded from environment / .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables live here — nothing is hardcoded at the call site."""

    model_config = SettingsConfigDict(
        env_prefix="SCANNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_host: str = "http://localhost:11434"
    triage_model: str = "gemma3:12b"
    embed_model: str = "nomic-embed-text"

    sec_user_agent: str = "stock-scanner/0.1 (contact@example.com)"

    # --- Alpaca (paper trading) ---
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    #: Equity at the start of the session, for the daily-loss guard. Left at 0
    #: the guard measures from current equity, which cannot detect a drawdown
    #: that happened before the process started.
    session_start_equity: float = 0.0
    audit_log_path: str = "logs/trading_audit.jsonl"
    #: Presence of this file blocks new submissions. The monitor can create it
    #: as an operator-visible circuit breaker; removing it is deliberate.
    kill_switch_path: str = "STOP_TRADING"

    #: Phone alerts via ntfy.sh (scanner.ops.notify). The topic is the only
    #: secret: long and random, set in .env. Empty means alerts print only.
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    finnhub_api_key: str = ""
    tiingo_api_key: str = ""

    # How long Ollama holds a model in VRAM after a call. "0" unloads
    # immediately, which is what you want when comparing two models on one
    # GPU: the default 5m keeps both resident and there is not room for that.
    # Keep Gemma resident between news-review calls. Override with e.g. "5m"
    # when VRAM should be released after inactivity; "-1" means indefinitely.
    #: How long Ollama holds the model after a call. "-1" means forever, which
    #: pins ~8.7GB of RAM and ~9.6GB of VRAM around the clock for a model used
    #: for a few seconds every half hour. "45m" outlives the 30-minute scan
    #: cadence — so the model stays warm all through the session and never
    #: reloads mid-run — then releases everything once scanning stops.
    llm_keep_alive: str = "45m"
    #: Ollama's `think` flag. None sends nothing (the model's own default).
    #: Gemma 4 thinks by default -- ~10x slower per triage batch and once an
    #: empty answer -- so .env sets this false for it. Checked on Ollama
    #: 0.35.1: think=false kept the JSON schema on gemma4:12b.
    llm_think: bool | None = None

    #: Interpreter of the separate TradingAgents venv (C:\Users\kitti\
    #: TradingAgents). Its packages never enter this one; see ta_worker.py.
    tradingagents_python: str = str(
        Path.home() / "TradingAgents" / ".venv" / "Scripts" / "python.exe")

    #: X recent search for the position card. Empty means the card says the
    #: feed is not configured. The token is not an order path.
    x_bearer_token: str = ""

    # Networking
    http_timeout_s: float = 30.0
    llm_timeout_s: float = 300.0

    # Triage behaviour
    triage_batch_size: int = 12
    triage_temperature: float = 0.0
    triage_num_ctx: int = 8192

    # Two headlines above this cosine similarity are treated as the same story.
    dedup_threshold: float = 0.92

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


settings = Settings()
