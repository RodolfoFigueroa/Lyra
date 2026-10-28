"""API-key validation and authorization dependencies."""

import logging

import ee
from google.oauth2.service_account import Credentials

from lyra_app.config import ConfigSecretError, EarthEngineConfig

logger = logging.getLogger(__name__)


def initialize_earth_engine(config: EarthEngineConfig) -> None:
    """Authenticate and initialize Earth Engine from runtime configuration.

    Raises:
        ConfigSecretError: If the service-account file cannot be loaded.
        RuntimeError: If Earth Engine initialization fails.
    """
    service_account_file = config.service_account_file

    try:
        credentials = Credentials.from_service_account_file(
            service_account_file,
            scopes=["https://www.googleapis.com/auth/earthengine"],
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        message = (
            f"Cannot load earth_engine.service_account_file: {service_account_file}"
        )
        raise ConfigSecretError(message) from exc
    try:
        ee.Initialize(credentials, project=config.project)
    except Exception as exc:
        message = f"Cannot initialize Earth Engine project: {config.project}"
        raise RuntimeError(message) from exc
    logger.info("Earth Engine initialized successfully!")
