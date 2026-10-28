"""Own loopback nginx prefix and uvicorn only; never touches a system service."""

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from yuliang.main import create_app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def proxy_application(d: dict[str, Any], directory: Path) -> Iterator[dict[str, Any]]:
    binary = shutil.which("nginx")
    if binary is None:
        raise RuntimeError("local nginx required; no image pull or system service fallback")
    prefix = directory / "nginx"
    (prefix / "logs").mkdir(parents=True)
    app = create_app(d["settings"])
    upstream = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )
    normal, fault = free_port(), free_port()
    while fault == normal:
        fault = free_port()
    cleanup: dict[str, Any] = {}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        app_port = int(listener.getsockname()[1])
        thread = threading.Thread(target=upstream.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not upstream.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not upstream.started:
            upstream.should_exit = True
            thread.join(timeout=8)
            raise AssertionError("private uvicorn did not start; own thread stopped")
        config = f"""worker_processes 1;
pid logs/nginx.pid;
error_log logs/error.log warn;
events {{ worker_connections 512; }}
http {{
 access_log off;
 gzip off;
 proxy_cache off;
 proxy_http_version 1.1;
 proxy_set_header Connection "";
 proxy_read_timeout 90s;
 proxy_send_timeout 90s;
 server {{ listen 127.0.0.1:{normal};
  location = /v1/events {{ proxy_buffering off; proxy_pass http://127.0.0.1:{app_port}; }}
 }}
 server {{ listen 127.0.0.1:{fault};
  location = /v1/events {{ proxy_buffering on; proxy_ignore_headers X-Accel-Buffering;
   limit_rate 1;
   proxy_pass http://127.0.0.1:{app_port}; }}
 }}
}}
"""
        (prefix / "nginx.conf").write_text(config, encoding="utf-8")
        arguments = [binary, "-p", str(prefix) + "/", "-c", "nginx.conf"]
        process: subprocess.Popen[bytes] | None = None
        try:
            subprocess.run(arguments + ["-t"], check=True, capture_output=True)  # noqa: S603
            process = subprocess.Popen(  # noqa: S603 -- fixed local nginx, no shell
                arguments + ["-g", "daemon off;"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 5
            while process.poll() is None and not listening(normal) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert process.poll() is None and listening(normal), "private nginx did not start"
            (prefix / "lifecycle.json").write_text(
                json.dumps(
                    dict(
                        nginxPid=process.pid,
                        appAndClientPid=os.getpid(),
                        normalPort=normal,
                        faultPort=fault,
                        upstreamPort=app_port,
                        rollback="terminate only own Popen handle; never nginx -s stop",
                    )
                ),
                encoding="utf-8",
            )
            version = subprocess.run(  # noqa: S603 -- discovered local binary, fixed flag
                [binary, "-v"], check=True, capture_output=True
            ).stderr.decode()
            yield dict(
                url=f"http://127.0.0.1:{normal}",
                fault=f"http://127.0.0.1:{fault}",
                upstream=f"http://127.0.0.1:{app_port}",
                port=normal,
                app_port=app_port,
                nginx_pid=process.pid,
                app=app,
                cleanup=cleanup,
                nginx_version=version.strip(),
                config_sha256=hashlib.sha256(config.encode()).hexdigest(),
            )
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    cleanup["forcedKill"] = True
                cleanup["ownNginxExited"] = process.poll() is not None
            upstream.should_exit = True
            thread.join(timeout=8)
            cleanup["ownUvicornExited"] = not thread.is_alive()
            cleanup["normalPortClosed"] = not listening(normal)
            cleanup["faultPortClosed"] = not listening(fault)
            cleanup["appPortClosed"] = not listening(app_port)
            (prefix / "cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
            assert not thread.is_alive(), "own uvicorn thread leaked"


async def raw_frames(response: httpx.Response) -> AsyncIterator[tuple[float, list[str]]]:
    buffer = b""
    async for chunk in response.aiter_raw():
        received = time.monotonic()
        buffer += chunk
        while b"\n\n" in buffer:
            frame, buffer = buffer.split(b"\n\n", 1)
            yield received, frame.decode("utf-8").splitlines()


async def first_frame(url: str, cookie: dict[str, str], cursor: str, timeout: float = 5) -> float:
    started = time.monotonic()
    async with httpx.AsyncClient(
        headers={"Origin": "http://testserver"}, cookies=cookie, timeout=10
    ) as client:
        async with client.stream(
            "GET", url + "/v1/events", headers={"Last-Event-ID": cursor}
        ) as response:
            assert response.status_code == 200
            arrived, frame = await asyncio.wait_for(anext(raw_frames(response)), timeout)
            assert frame == [": connected"]
            return arrived - started


def process_sample(pid: int) -> dict[str, int]:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        rss = next(
            int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")
        )
        stat = Path(f"/proc/{pid}/stat").read_text().split()
        return dict(
            rssKiB=rss,
            fdCount=len(list(Path(f"/proc/{pid}/fd").iterdir())),
            cpuTicks=int(stat[13]) + int(stat[14]),
        )
    except (FileNotFoundError, ProcessLookupError):
        return dict(rssKiB=0, fdCount=0, cpuTicks=0)


def resources(h: dict[str, Any]) -> dict[str, Any]:
    children = Path(f"/proc/{h['nginx_pid']}/task/{h['nginx_pid']}/children").read_text().split()
    tcp = Path("/proc/net/tcp").read_text().splitlines()[1:]

    def count(port: int) -> int:
        return sum(
            int(row.split()[1].split(":")[1], 16) == port and row.split()[3] == "01" for row in tcp
        )

    return dict(
        nginxMaster=process_sample(h["nginx_pid"]),
        nginxWorkers=[process_sample(int(pid)) for pid in children],
        appAndClientProcess=process_sample(os.getpid()),
        downstreamEstablished=count(h["port"]),
        upstreamEstablished=count(h["app_port"]),
    )
