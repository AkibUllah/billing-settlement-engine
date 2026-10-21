"""Helpers for host related settings on managed hosting (Render, Codespaces)."""

from __future__ import annotations


def split_list(raw: str) -> list[str]:
    """Parse a comma-separated environment value, dropping blanks."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def allowed_hosts(configured: list[str], render_hostname: str | None) -> list[str]:
    """Append Render's public hostname (``RENDER_EXTERNAL_HOSTNAME``) to the configured hosts."""
    hosts = list(configured)
    if render_hostname and render_hostname not in hosts:
        hosts.append(render_hostname)
    return hosts


def csrf_trusted_origins(configured: list[str], render_hostname: str | None) -> list[str]:
    """Trusted origins for CSRF checks: configured values plus ``https://<render hostname>``."""
    origins = list(configured)
    if render_hostname:
        origin = f"https://{render_hostname}"
        if origin not in origins:
            origins.append(origin)
    return origins
