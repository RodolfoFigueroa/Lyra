"""Pure resource configuration and credential reference declarations."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IOConfig(BaseModel):
    """Configuration for a resource, empty unless extended by its author."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SecretReference(IOConfig):
    """Locate a credential in an environment variable or UTF-8 file."""

    env: str | None = Field(
        default=None, description="Environment variable containing the credential."
    )
    file: Path | None = Field(
        default=None, description="UTF-8 file containing the credential."
    )

    @model_validator(mode="after")
    def validate_source(self) -> "SecretReference":
        """Require exactly one credential source.

        Returns:
            This reference with exactly one source.

        Raises:
            ValueError: If resource configuration is invalid.
        """
        if (self.env is None) == (self.file is None):
            message = "Specify exactly one of env or file"
            raise ValueError(message)
        return self


class ResourceConfig(IOConfig):
    """Deployment configuration for one named resource."""

    config: dict[str, object] = Field(
        default_factory=dict,
        description="Values validated against the resource configuration model.",
    )
    secrets: dict[str, SecretReference] = Field(
        default_factory=dict,
        description="Named credential references resolved during execution.",
    )
