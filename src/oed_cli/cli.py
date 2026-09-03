"""Click command surface for ``oed``.

Reserved (built-in) commands:
    oed --version
    oed info
    oed services
    oed schema <service>[.<method>]
    oed cache {show,clear,refresh}
    oed auth {status,token,logout,login,login --manual}

Dynamic dispatch (``oed <service> <method> ...``) lives in :mod:`oed_cli.main`
and is documented in ``docs/cli-design.md`` (v0.2).

``oed --help`` additionally lists every auto-discovered service so newcomers
can see what the CLI exposes without reading docs first.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Any

import click

from . import __version__
from .auth import auth_group, run_login
from .discovery import (
    CACHE_TTL_SECONDS,
    DiscoveryFeed,
    ServiceMeta,
    current_community,
    fetch_discovery,
    fetch_spec,
)
from .errors import OedError
from .http import resolve_gateway


def _print_json(data: Any, *, error: bool = False) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2), err=error, color=False)


def _resolve_feed(*, force_refresh: bool) -> DiscoveryFeed:
    try:
        return fetch_discovery(community=None, force_refresh=force_refresh)
    except OedError as exc:
        _print_json(exc.to_dict(), error=True)
        sys.exit(exc.code)


class OedCli(click.Group):
    """Click group that decorates ``--help`` with the live discovery feed.

    Falls back gracefully if the gateway is unreachable / the cache is empty:
    no extra section in that case, the standard help still renders.
    """

    def get_help(self, ctx: click.Context) -> str:
        original = super().get_help(ctx)
        extra = self._services_help_section()
        return f"{original}\n\n{extra}" if extra else original

    @staticmethod
    def _services_help_section() -> str | None:
        try:
            feed = fetch_discovery()
        except Exception:
            return None
        community = current_community()
        services = feed.for_community(community)
        if not services:
            return None

        widths = {"name": max(len(s.service_name) for s in services)}
        rows: list[str] = []
        for s in services:
            desc = (s.description or s.title or "").strip().split("\n", 1)[0]
            if len(desc) > 78:
                desc = desc[:75] + "..."
            rows.append(f"  {s.service_name.ljust(widths['name'])}    {desc}")

        first = services[0].service_name
        title = (
            f"Auto-discovered services (community='{community}', "
            f"{len(services)} service{'' if len(services) == 1 else 's'} "
            f"resolved live from the gateway):"
        )
        return (
            f"{title}\n\n"
            + "\n".join(rows)
            + "\n\n"
            "Every declared query / path parameter on an operation is exposed\n"
            "as its own --<kebab-case> flag. Use `oed <service> --help` to list\n"
            "operations, then `oed <service> <operation> --help` to see the\n"
            "flags for that operation. For the full spec: `oed schema <service>`.\n"
            "\n"
            "Quickstart:\n"
            f"  oed {first}                            # list operations\n"
            f"  oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082\n"
            f"  oed meeting listMeetings --date 2026-07-29\n"
        )


def _format_service(service: ServiceMeta, *, age_s: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": service.name,
        "service_name": service.service_name,
        "community": service.community,
        "title": service.title,
        "version": service.version,
    }
    if service.description:
        out["description"] = service.description
    if service.base_url:
        out["base_url"] = service.base_url
    if age_s is not None:
        out["fetched_seconds_ago"] = round(age_s, 1)
    return out


@click.group(
    cls=OedCli,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=__version__, prog_name="oed")
@click.option(
    "--no-color",
    is_flag=True,
    default=True,
    help="Disable ANSI colors in output (default: true, AI-friendly).",
)
@click.pass_context
def cli(ctx: click.Context, no_color: bool) -> None:
    """oed — openEuler Infra command line. Auto-discovered, AI-friendly."""
    ctx.ensure_object(dict)
    ctx.obj["no_color"] = no_color


# Register reserved sub-groups from sibling modules.
cli.add_command(auth_group)


@cli.command("login")
@click.option(
    "--manual",
    "-m",
    is_flag=True,
    help="Paste token + cookie from terminal instead of using device-flow.",
)
def login_cmd(manual: bool) -> None:
    """Shortcut for ``oed auth login`` (device-flow login)."""

    run_login(manual)


@cli.command("info")
def info() -> None:
    """Show gateway reachability and the discovery snapshot summary."""
    import time

    feed = _resolve_feed(force_refresh=False)
    age = time.time() - feed.fetched_at

    services = feed.services
    payload = {
        "ok": True,
        "oed_version": __version__,
        "gateway": resolve_gateway(),
        "community": current_community(),
        "services_total": len(services),
        "communities_seen": sorted({s.community for s in services}),
        "cache": {
            "ttl_seconds": CACHE_TTL_SECONDS,
            "stale": age >= CACHE_TTL_SECONDS,
            "fetched_seconds_ago": round(age, 1),
            "fetched_at_iso": dt.datetime.fromtimestamp(
                feed.fetched_at, tz=dt.timezone.utc
            ).isoformat(),
        },
    }
    _print_json(payload)


@cli.command("services")
@click.option("--community", default=None, help="Limit to one community (default: current).")
@click.option("--refresh", is_flag=True, help="Force re-fetch the discovery feed first.")
def services_cmd(community: str | None, refresh: bool) -> None:
    """List services in the discovery feed."""
    import time

    feed = _resolve_feed(force_refresh=refresh)
    target = community or current_community()
    items = [s for s in feed.services if s.community == target]
    age = time.time() - feed.fetched_at
    _print_json([_format_service(s, age_s=age) for s in items])


@cli.command("schema")
@click.argument("target")
@click.option("--refresh", is_flag=True, help="Force re-fetch the discovery feed first.")
def schema_cmd(target: str, refresh: bool) -> None:
    """Print OpenAPI 3.x spec for SERVICE[.METHOD] from the current community."""
    if "." in target:
        service_name, method = target.split(".", 1)
    else:
        service_name, method = target, None

    feed = _resolve_feed(force_refresh=refresh)
    community = current_community()
    try:
        service = feed.find_service(community, service_name)
    except OedError as exc:
        _print_json(exc.to_dict(), error=True)
        sys.exit(exc.code)

    try:
        spec = fetch_spec(service)
    except OedError as exc:
        _print_json(exc.to_dict(), error=True)
        sys.exit(exc.code)

    if method is None:
        _print_json(spec)
        return

    paths: dict[str, Any] = spec.get("paths", {})
    matches: list[dict[str, Any]] = []
    for path, item in paths.items():
        for verb, op in item.items():
            if verb.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if op.get("operationId") == method or method in (path, f"{verb.upper()} {path}"):
                matches.append({"path": path, "method": verb.upper(), "operation": op})
    if not matches:
        _print_json(
            {
                "ok": False,
                "code": 4,
                "error": "method_not_found",
                "message": f"no path matches method '{method}' on service '{service_name}'",
                "available_paths": sorted(paths.keys()),
            },
            error=True,
        )
        sys.exit(4)

    _print_json({"service": service.name, "matches": matches})


@cli.group()
def cache() -> None:
    """Manage the local discovery cache."""


@cache.command("show")
def cache_show() -> None:
    from .discovery import _cache_path

    p = _cache_path()
    if not p.is_file():
        _print_json({"cached": False, "path": str(p)})
        return
    stat = p.stat()
    _print_json(
        {
            "cached": True,
            "path": str(p),
            "size_bytes": stat.st_size,
            "modified_iso": dt.datetime.fromtimestamp(
                stat.st_mtime, tz=dt.timezone.utc
            ).isoformat(),
            "ttl_seconds": CACHE_TTL_SECONDS,
        }
    )


@cache.command("clear")
def cache_clear() -> None:
    from .discovery import _cache_path

    p = _cache_path()
    if p.is_file():
        p.unlink()
    _print_json({"ok": True, "cleared": str(p)})


@cache.command("refresh")
def cache_refresh() -> None:
    feed = fetch_discovery(community=None, force_refresh=True)
    _print_json({"ok": True, "services_total": len(feed.services)})


if __name__ == "__main__":
    cli()
