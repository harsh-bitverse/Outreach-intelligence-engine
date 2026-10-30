"""
Application Configuration and Settings Module.
Handles environment variables, default paths, rate limits, and safety flags.
"""

from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration settings for the Outreach Intelligence Engine."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    KNOWLEDGE_BASE_DIR: Path = BASE_DIR / "data" / "knowledge_base"
    LOGS_DIR: Path = BASE_DIR / "logs"

    # Master Authoritative Excel Workbook
    MASTER_WORKBOOK_PATH: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "private" / "Summer_2027_Professor_Database_WITH_TIME_BLOCKS_EXPORT.xlsx"
    )
    SQLITE_DB_PATH: Path = DATA_DIR / "outreach_engine.db"

    # Authoritative Attachment Documents
    CV_ATTACHMENT_PATH: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "private" / "Harshith_N_Research_CV_Summer2027.pdf"
    )
    RESEARCH_INTERESTS_PATH: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "private" / "Harshith_N_Research_Interests_Summer2027.pdf"
    )
    CV_ATTACHMENT_FILENAME: str = "Harshith_N_Research_CV_Summer2027.pdf"
    RESEARCH_INTERESTS_FILENAME: str = "Harshith_N_Research_Interests_Summer2027.pdf"

    # Application Mode
    APP_ENV: str = "DEVELOPMENT"
    DEBUG: bool = True

    # IIT Delhi Mail Configurations
    IITD_IMAP_SERVER: str = "mailstore.iitd.ac.in"
    IITD_IMAP_PORT: int = 993
    IITD_IMAP_USE_SSL: bool = True

    IITD_SMTP_SERVER: str = "smtp.iitd.ac.in"
    IITD_SMTP_PORT: int = 465
    IITD_SMTP_USE_SSL: bool = True

    IITD_EMAIL_USER: str = "ch1251635@iitd.ac.in"
    IITD_EMAIL_PASSWORD: Optional[str] = None

    # Safety, Sending Controls & Permissions
    # Initial outreach sending is strictly DISABLED by default.
    GATEWAY_ENABLED: bool = False
    DRY_RUN: bool = True
    CAMPAIGN_AUTHORIZED: bool = False
    IITD_CSC_APPROVED: bool = False
    EMERGENCY_STOP: bool = False

    # Rate Limiting & 90-Minute Window Cadence
    MAX_EMAILS_PER_BATCH: int = 5
    BATCH_SIZE: int = 5
    COOLING_BREAK_SECONDS: int = 60
    MAX_EMAILS_PER_DAY: int = 25
    DAILY_MAX_OUTREACH: int = 25
    MIN_INTERVAL_SECONDS_BETWEEN_EMAILS: int = 30
    DISPATCH_WINDOW_MINUTES: int = 90

    # Operator Dashboard
    OPERATOR_TIMEZONE: str = "Asia/Kolkata"  # IST
    TARGET_LOCAL_START_HOUR: int = 8  # 8:00 AM local
    TARGET_LOCAL_END_HOUR: int = 9    # 9:00 AM local

    # Optional OpenAI Configuration
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"


# Singleton configuration instance
settings = Settings()

# Ensure critical directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
