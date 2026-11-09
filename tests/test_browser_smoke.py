"""Playwright + axe browser smoke tests (opt-in via ``pytest -m browser``).

The default test run excludes these with ``addopts = -m "not browser"``. They
boot a real uvicorn server against a temporary ``vids/``/``stats/`` tree with
authentication off, then exercise every tab and the modal dialogs. In the Nix
dev shell Playwright and Chromium are provided, so run ``pytest -m browser``.
On a pipenv setup, install the browser once with
``pipenv run playwright install chromium`` before ``pipenv run pytest -m browser``.

The axe scans skip gracefully when ``axe-playwright-python`` is not installed;
the Playwright smoke tests skip when Playwright itself is unavailable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

pytestmark = pytest.mark.browser

_REPO_ROOT: Path = Path(__file__).parent.parent
_TABS: list[str] = [
    "tag-film", "team-eval", "combine-clips", "search", "pca", "trend", "audit",
]


def _free_port() -> int:
    """Return an unused localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout: float = 30.0) -> None:
    """Poll ``url`` until the server answers or the timeout expires."""
    deadline: float = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except urllib.error.URLError:
            time.sleep(0.1)
    raise RuntimeError(f"Server at {url} did not start within {timeout}s")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run uvicorn in a temp data tree and yield its base URL."""
    root: Path = tmp_path_factory.mktemp("browser")
    for relative in (
        "vids/untaged", "vids/taged", "vids/clips",
        "stats/wrestler_data", "stats/reports", "stats/tmp", "cfg",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "vids" / "untaged" / "sample.mkv").write_bytes(b"not a real video")
    for name in ("config.json", "Wrestlers.json.example"):
        source: Path = _REPO_ROOT / "cfg" / name
        target: Path = root / "cfg" / (
            "Wrestlers.json" if name.endswith(".example") else name
        )
        if source.is_file():
            shutil.copyfile(source, target)

    env: dict[str, str] = os.environ.copy()
    env["MATSTAT_AUTH"] = "off"
    env["MATSTAT_ROOT"] = str(root)
    # Keep the dev-shell site-packages on the path (Nix provides dependencies
    # through PYTHONPATH) while making the repo importable from the temp cwd.
    existing_path: str = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_REPO_ROOT)] + ([existing_path] if existing_path else [])
    )
    port: int = _free_port()
    base_url: str = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "scripts.web.app:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_server(f"{base_url}/api/config")
        yield base_url
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


@pytest.fixture(scope="module")
def browser(live_server: str) -> Iterator[Any]:
    """A headless Chromium instance shared by the module's tests."""
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def app_page(browser: Any, live_server: str) -> Iterator[tuple[Any, list[str]]]:
    """An app page plus a list that collects uncaught page errors."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_server, wait_until="networkidle")
    # Wait for the SPA boot to mount the first tab; networkidle alone can fire
    # before deferred module code has attached the dialog/tab listeners.
    page.locator("#tag-film-root > *").first.wait_for(state="attached")
    try:
        yield page, errors
    finally:
        context.close()


def _open_tab(page: Any, tab: str) -> None:
    """Click a tab button and wait for its pane to become visible."""
    page.locator(f'.tab-btn[data-tab="{tab}"]').click()
    page.locator(f"#tab-{tab}").wait_for(state="visible")


class TestAppBoot:
    """The SPA boots and every tab mounts."""

    def test_every_tab_mounts(self, app_page: tuple[Any, list[str]]) -> None:
        page, _errors = app_page
        assert page.title() == "MatStat"
        for tab in _TABS:
            _open_tab(page, tab)
            root = page.locator(f"#{tab}-root")
            assert root.locator("*").count() > 0
            assert page.locator(f'.tab-btn[data-tab="{tab}"]').get_attribute(
                "aria-selected"
            ) == "true"

    def test_no_uncaught_page_errors(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, errors = app_page
        for tab in _TABS:
            _open_tab(page, tab)
            page.wait_for_timeout(100)
        assert errors == []


class TestDialogs:
    """Modal dialogs open and close without error."""

    def test_about_dialog(self, app_page: tuple[Any, list[str]]) -> None:
        page, _errors = app_page
        page.locator("#about-btn").click()
        dialog = page.locator("#about-dialog")
        dialog.wait_for(state="visible")
        assert "MatStat" in dialog.inner_text()
        page.locator("#about-dialog button[value='close']").click()
        dialog.wait_for(state="hidden")

    def test_delete_confirmation_dialog(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        _open_tab(page, "tag-film")
        page.locator(".video-item", has_text="sample.mkv").first.click()
        page.locator("#tab-tag-film").locator(
            "button", has_text="Delete Video"
        ).click()
        dialog = page.locator("dialog.modal").last
        dialog.wait_for(state="visible")
        assert "sample.mkv" in dialog.inner_text()
        page.locator("dialog.modal button", has_text="Cancel").last.click()
        dialog.wait_for(state="hidden")
        assert page.locator(".video-item", has_text="sample.mkv").count() > 0


class TestHiddenInvariant:
    """P0-1: ``[hidden]`` must always win over author display rules."""

    def test_hidden_elements_are_not_displayed(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        for selector in ("#ruleset-banner", "#tab-search", "#tab-audit"):
            locator = page.locator(selector)
            assert locator.is_hidden(), selector
            display = locator.evaluate(
                "(node) => getComputedStyle(node).display"
            )
            assert display == "none", selector


class TestViewportFit:
    """Cards and panes fit the viewport instead of scrolling the whole page.

    Desktop panes own their scrolling, so the document and ``#tab-content``
    must not grow past the viewport. Narrow layouts may scroll vertically but
    must never overflow sideways.
    """

    _DESKTOP: tuple[tuple[int, int], ...] = (
        (1440, 900), (1280, 800), (1024, 768),
    )
    _NARROW: tuple[int, int] = (390, 844)

    @staticmethod
    def _metrics(page: Any) -> dict[str, int]:
        """Return document and content-area scroll/client dimensions."""
        return page.evaluate(
            """() => {
              const doc = document.documentElement;
              const content = document.querySelector('#tab-content');
              return {
                docScrollW: doc.scrollWidth,
                docClientW: doc.clientWidth,
                docScrollH: doc.scrollHeight,
                docClientH: doc.clientHeight,
                contentScrollW: content.scrollWidth,
                contentClientW: content.clientWidth,
                contentScrollH: content.scrollHeight,
                contentClientH: content.clientHeight,
              };
            }"""
        )

    def test_desktop_tabs_fit_without_page_scroll(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        for width, height in self._DESKTOP:
            page.set_viewport_size({"width": width, "height": height})
            for tab in _TABS:
                _open_tab(page, tab)
                page.wait_for_timeout(100)
                metrics = self._metrics(page)
                label = f"{width}x{height} {tab}: {metrics}"
                assert metrics["docScrollW"] <= metrics["docClientW"] + 1, label
                assert (
                    metrics["contentScrollW"] <= metrics["contentClientW"] + 1
                ), label
                assert metrics["docScrollH"] <= metrics["docClientH"] + 1, label
                assert (
                    metrics["contentScrollH"] <= metrics["contentClientH"] + 1
                ), label

    def test_narrow_layout_never_overflows_sideways(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        width, height = self._NARROW
        page.set_viewport_size({"width": width, "height": height})
        for tab in _TABS:
            _open_tab(page, tab)
            page.wait_for_timeout(100)
            metrics = self._metrics(page)
            label = f"{width}x{height} {tab}: {metrics}"
            assert metrics["docScrollW"] <= metrics["docClientW"] + 1, label
            assert metrics["contentScrollW"] <= metrics["contentClientW"] + 1, label

    def test_video_selector_fills_its_card(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        _open_tab(page, "tag-film")
        page.locator(
            "#tab-tag-film .video-card .check-list li"
        ).first.wait_for(state="attached")
        metrics: dict[str, int] = page.evaluate(
            """() => {
              const card = document.querySelector(
                '#tab-tag-film .video-card'
              );
              const list = card.querySelector('.check-list');
              return {
                cardH: card.getBoundingClientRect().height,
                listH: list.getBoundingClientRect().height,
              };
            }"""
        )
        assert metrics["listH"] > metrics["cardH"] * 0.3, metrics

    def test_pca_plot_sits_in_left_column(
        self, app_page: tuple[Any, list[str]]
    ) -> None:
        page, _errors = app_page
        _open_tab(page, "pca")
        assert page.locator("#tab-pca .tag-left .plot-card").count() == 1
        assert page.locator("#tab-pca .tag-right .plot-card").count() == 0
        assert page.locator("#tab-pca .tag-right .player-card").count() == 1


class TestAccessibility:
    """axe-core scans for each tab; skips when axe is not installed."""

    @pytest.fixture
    def axe(self) -> Any:
        module = pytest.importorskip(
            "axe_playwright_python.sync_playwright"
        )
        return module.Axe()

    def _serious(self, results: Any) -> list[dict[str, Any]]:
        violations: Any = getattr(results, "violations", None)
        if violations is None:
            violations = getattr(results, "response", {}).get("violations", [])
        return [
            violation for violation in violations
            if violation.get("impact") in ("critical", "serious")
        ]

    def test_each_tab_has_no_serious_violations(
        self, app_page: tuple[Any, list[str]], axe: Any
    ) -> None:
        page, _errors = app_page
        for tab in _TABS:
            _open_tab(page, tab)
            page.wait_for_timeout(150)
            results = axe.run(page)
            serious = self._serious(results)
            assert not serious, f"{tab}: {results.generate_report()}"

    def test_about_dialog_has_no_serious_violations(
        self, app_page: tuple[Any, list[str]], axe: Any
    ) -> None:
        page, _errors = app_page
        page.locator("#about-btn").click()
        page.locator("#about-dialog").wait_for(state="visible")
        results = axe.run(page)
        assert not self._serious(results), results.generate_report()
