import asyncio
from contextlib import asynccontextmanager
import gzip
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import aiohttp
from aiohttp import web
import pytest

from akdp import _arkprts, fetch


@pytest.fixture
def archive_server(monkeypatch):
    """Serve real HTTP locally while recording the requested codeload URL."""
    payload = gzip.compress(b"archive bytes" * 100)
    requested = []

    @asynccontextmanager
    async def serve(status=200):
        async def handler(request):
            if status != 200:
                return web.Response(status=status)
            response = web.StreamResponse(headers={"Content-Encoding": "gzip"})
            await response.prepare(request)
            await response.write(payload[:10])
            await response.write(payload[10:])
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_get("/archive", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            url = f"http://127.0.0.1:{runner.addresses[0][1]}/archive"
            original_get = aiohttp.ClientSession.get

            def get(session, target, **kwargs):
                requested.append(target)
                return original_get(session, url, **kwargs)

            monkeypatch.setattr(aiohttp.ClientSession, "get", get)
            yield payload, requested
        finally:
            await runner.cleanup()

    return serve


@pytest.mark.parametrize("branch", ["main", "YoStar", None])
def test_archive_download_uses_codeload_and_preserves_gzip(tmp_path, archive_server, branch):
    destination = tmp_path / "nested/archive.tar.gz"

    async def run():
        async with archive_server() as (payload, requested):
            kwargs = {} if branch is None else {"branch": branch}
            result = await _arkprts.download_github_tarball(
                "MooncellWiki/OpenArknightsFBS", destination, **kwargs)
            assert result == destination
            assert result.read_bytes() == payload
            assert requested == [
                f"https://codeload.github.com/MooncellWiki/OpenArknightsFBS/tar.gz/{branch or 'HEAD'}"
            ]

    asyncio.run(run())


def test_archive_download_supports_temporary_destination(tmp_path, monkeypatch, archive_server):
    monkeypatch.setattr(_arkprts.tempfile, "tempdir", str(tmp_path))

    async def run():
        async with archive_server() as (payload, _):
            result = await _arkprts.download_github_tarball("owner/repo", None)
            assert isinstance(result, Path)
            assert result.parent == tmp_path
            assert result.read_bytes() == payload

    asyncio.run(run())


def test_archive_download_propagates_http_failure(tmp_path, archive_server):
    destination = tmp_path / "archive.tar.gz"

    async def run():
        async with archive_server(status=503):
            with pytest.raises(aiohttp.ClientResponseError) as error:
                await _arkprts.download_github_tarball("owner/repo", destination)
            assert error.value.status == 503
            assert not destination.exists()

    asyncio.run(run())


def test_adapter_installs_downloader_before_running_upstream_cli(monkeypatch):
    monkeypatch.setattr(_arkprts.git, "download_github_tarball", Mock())
    argv = ["akdp._arkprts", "output", "--server", "cn", "--log-level", "INFO"]
    monkeypatch.setattr(sys, "argv", argv)

    def run_module(name, *, run_name):
        assert _arkprts.git.download_github_tarball is _arkprts.download_github_tarball
        assert sys.argv == argv

    run_module = Mock(side_effect=run_module)
    monkeypatch.setattr(_arkprts.runpy, "run_module", run_module)
    _arkprts.main()
    run_module.assert_called_once_with("arkprts.assets", run_name="__main__")


@pytest.mark.parametrize("success_on", [1, 2, None])
def test_fetch_uses_adapter_with_existing_retry_policy(tmp_path, monkeypatch, success_on):
    extract = tmp_path / "extract"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "text": True}
        return subprocess.CompletedProcess(
            command, 0 if len(calls) == success_on else 1, "", "download failed")

    monkeypatch.setattr(fetch.subprocess, "run", run)
    sleep = Mock()
    monkeypatch.setattr(fetch.time, "sleep", sleep)
    if success_on is None:
        with pytest.raises(RuntimeError, match="arkprts failed after 5 attempts. Tail:\n"):
            fetch.run_arkprts(extract, server="cn")
    else:
        fetch.run_arkprts(extract, server="cn")
    count = success_on or 5
    assert calls == [[sys.executable, "-m", "akdp._arkprts", str(extract),
                      "--server", "cn", "--log-level", "INFO"]] * count
    assert [call.args[0] for call in sleep.call_args_list] == [30 * i for i in range(1, count)]
