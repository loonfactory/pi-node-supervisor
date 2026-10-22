"""Reject packages that would silently omit the web application."""

from pathlib import Path
from html.parser import HTMLParser

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"src", "href"} and value and value.startswith("/assets/"):
                self.paths.append(value.lstrip("/"))


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        static = Path(self.root) / "src/pi_node_supervisor/web/static"
        index = static / "index.html"
        message = "Frontend assets are missing. Run scripts/build-frontend.sh before packaging."
        if not index.is_file():
            raise RuntimeError(message)
        parser = AssetReferences()
        parser.feed(index.read_text(encoding="utf-8"))
        if not parser.paths or any(
            not (static / path).is_file() for path in parser.paths
        ):
            raise RuntimeError(message)
