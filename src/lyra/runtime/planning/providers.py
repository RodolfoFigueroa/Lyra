"""Manifest-only selection of the enabled location provider."""

from collections.abc import Mapping
from dataclasses import dataclass

from lyra.contracts import providers
from lyra.contracts.errors import ResourceConfigurationError


@dataclass(frozen=True)
class SelectedLocationProvider:
    """The sole enabled plugin's provider and named resource configuration key."""

    distribution: str
    declaration: providers.LocationProviderManifest

    @property
    def resource(self) -> str:
        """Resource key used with the existing ResourceScope configuration."""
        return self.declaration.resource


def select_location_provider(
    declarations: Mapping[str, providers.LocationProviderManifest | None],
    *,
    required: bool = False,
) -> SelectedLocationProvider | None:
    """Select the sole declaration among enabled plugins without acquisition.

    Deployment plugin enablement and LocalRunner plugin selection determine the
    candidates. Multiple declarations are ambiguous even if resources match.
    Settings and credentials remain under the selected resource's existing key.

    Returns:
        The selected provider, or None when absent and not required.

    Raises:
        ResourceConfigurationError: If providers are ambiguous or required but absent.
    """
    candidates = [
        SelectedLocationProvider(distribution, declaration)
        for distribution, declaration in sorted(declarations.items())
        if declaration is not None
    ]
    if len(candidates) > 1:
        names = ", ".join(candidate.distribution for candidate in candidates)
        message = f"Multiple active location providers: {names}"
        raise ResourceConfigurationError(message)
    if not candidates:
        if required:
            message = "Location resolution requires a configured location provider"
            raise ResourceConfigurationError(message)
        return None
    return candidates[0]
