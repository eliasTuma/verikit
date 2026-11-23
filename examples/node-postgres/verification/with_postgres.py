"""Run a test command against a throwaway PostgreSQL in Docker, and always remove it.

    python verification/with_postgres.py node --test test/coupon.race.test.js

1. If COUPON_DATABASE_URL is already set, Docker is not used: the command runs against that
   database (a team's own test database, a CI service container).
2. Otherwise this starts postgres:16-alpine under a unique name, on a free port of 127.0.0.1,
   with its data in memory, waits until it answers on that port, and runs the command with
   COUPON_DATABASE_URL pointing at it.
3. The container is removed when the command ends, fails or runs too long, and on Ctrl+C.
   If this script itself is killed, the container stops and removes itself after
   WITH_POSTGRES_MAX_LIFETIME seconds (it was started with `--rm` under `timeout`).

Exit code: the command's own; 124 if it ran longer than WITH_POSTGRES_TIMEOUT seconds;
2 for a setup problem (no docker, daemon not running, PostgreSQL did not start, the
container could not be removed); 130 after Ctrl+C.

Python standard library only. Docker must run Linux containers.
"""
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

IMAGE = "postgres:16-alpine"
URL_ENV = "COUPON_DATABASE_URL"
LABEL = "verikit.example=node-postgres"
DATABASE = "coupons"
TEST_TIMEOUT = int(os.environ.get("WITH_POSTGRES_TIMEOUT", "180"))        # the test command
MAX_LIFETIME = int(os.environ.get("WITH_POSTGRES_MAX_LIFETIME", "900"))   # the container, whatever happens
PULL_TIMEOUT = 300
READY_TIMEOUT = 90


class SetupError(Exception):
    pass


def say(msg: str) -> None:
    print(f"[with_postgres] {msg}", flush=True)


def docker(exe: str, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"`docker {args[0]}` did not answer within {timeout}s"


def find_docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise SetupError(f"`docker` is not on PATH. Install Docker (or Docker Desktop) and start it, "
                         f"or set {URL_ENV} to a PostgreSQL test database.")
    # `docker` on PATH says nothing about the daemon: ask the server for its version.
    code, out = docker(exe, "version", "--format", "{{.Server.Os}} {{.Server.Version}}", timeout=30)
    if code != 0:
        raise SetupError(f"`docker` is installed but its daemon does not answer. Start Docker Desktop "
                         f"(or the docker service), or set {URL_ENV} to a PostgreSQL test database.\n"
                         f"docker said: {out[-600:]}")
    if not out.startswith("linux"):
        raise SetupError(f"Docker is running {out.split()[0]} containers; {IMAGE} needs Linux containers.")
    return exe


def speaks_postgres(port: int) -> bool:
    """True when a PostgreSQL server answers on the published port. Sends the protocol's
    SSLRequest; a server replies with one byte, S or N. A bare TCP connect is not enough:
    Docker's port forwarder accepts connections before the server listens."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.settimeout(2)
            s.sendall(struct.pack("!ii", 8, 80877103))
            return s.recv(1) in (b"S", b"N")
    except OSError:
        return False


def start_postgres(exe: str, name: str, password: str) -> int:
    if docker(exe, "image", "inspect", IMAGE)[0] != 0:
        say(f"pulling {IMAGE} (first run on this machine)")
        code, out = docker(exe, "pull", IMAGE, timeout=PULL_TIMEOUT)
        if code != 0:
            raise SetupError(f"could not pull {IMAGE}: {out[-600:]}")
    # --rm + `timeout`: the container removes itself after MAX_LIFETIME seconds even if this
    # script is killed. busybox `timeout` keeps PostgreSQL as the main process and sends it
    # SIGINT (fast shutdown). --tmpfs: data in memory, no volume left behind.
    code, out = docker(
        exe, "run", "-d", "--rm", "--name", name, "--label", LABEL,
        "-e", f"POSTGRES_PASSWORD={password}", "-e", f"POSTGRES_DB={DATABASE}",
        "-p", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data",
        "--entrypoint", "timeout", IMAGE,
        "-s", "INT", str(MAX_LIFETIME), "docker-entrypoint.sh", "postgres", "-c", "fsync=off",
        timeout=120)
    if code != 0:
        raise SetupError(f"could not start {IMAGE}: {out[-600:]}")
    code, out = docker(exe, "port", name, "5432/tcp")
    try:
        port = int(out.splitlines()[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        raise SetupError(f"could not read the published port of {name}: {out[-300:]}") from None

    # The image first runs a setup server that listens on a socket file only, then restarts.
    # So ask over TCP inside the container, then from this side of the published port.
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        code, _ = docker(exe, "exec", name, "pg_isready", "-q", "-h", "127.0.0.1", "-U", "postgres", "-d", DATABASE)
        if code == 0 and speaks_postgres(port):
            return port
        if docker(exe, "inspect", "-f", "{{.State.Running}}", name)[1] != "true":
            raise SetupError(f"the PostgreSQL container {name} stopped while starting:\n"
                             f"{docker(exe, 'logs', '--tail', '30', name)[1]}")
        time.sleep(0.3)
    raise SetupError(f"PostgreSQL in {name} did not accept connections within {READY_TIMEOUT}s:\n"
                     f"{docker(exe, 'logs', '--tail', '30', name)[1]}")


def remove(exe: str, name: str) -> bool:
    """Remove the container by its own name (never anything else). True when it is gone."""
    for _ in range(3):
        docker(exe, "rm", "-f", "-v", name, timeout=60)
        code, out = docker(exe, "ps", "-a", "-q", "--filter", f"name=^/{name}$")
        if code == 0 and not out:
            return True
        time.sleep(1)
    return False


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_command(argv: list[str], env: dict[str, str]) -> int:
    exe = shutil.which(argv[0])
    if not exe:
        raise SetupError(f"`{argv[0]}` is not on PATH")
    sys.stdout.flush()
    # Output goes straight to this script's stdout/stderr. On POSIX the command gets its own
    # process group so that a timeout can stop it together with its children.
    proc = subprocess.Popen([exe, *argv[1:]], env=env, stdin=subprocess.DEVNULL,
                            start_new_session=(os.name != "nt"))
    deadline = time.time() + TEST_TIMEOUT
    try:
        while time.time() < deadline:
            try:
                return proc.wait(timeout=0.5)   # short waits, so Ctrl+C is seen at once on Windows too
            except subprocess.TimeoutExpired:
                pass
        kill_tree(proc)
        say(f"ERROR: the command ran longer than {TEST_TIMEOUT}s and was stopped (WITH_POSTGRES_TIMEOUT)")
        return 124
    except BaseException:
        kill_tree(proc)
        raise


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        say("usage: python verification/with_postgres.py <test command...>")
        return 2

    if os.environ.get(URL_ENV):
        say(f"{URL_ENV} is set: using that database, Docker is not used")
        try:
            return run_command(argv, dict(os.environ))
        except SetupError as e:
            say(f"ERROR: {e}")
            return 2

    # Ctrl+C, Ctrl+Break, `kill` and a closed terminal all take the same road to `finally`.
    stop_signals = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP") if hasattr(signal, n)]
    for sig in stop_signals:
        signal.signal(sig, interrupt)

    name = f"verikit-coupon-pg-{os.getpid()}-{secrets.token_hex(3)}"
    password = secrets.token_hex(12)
    exe = None
    started = False
    code = 2
    try:
        exe = find_docker()
        started = True
        port = start_postgres(exe, name, password)
        say(f"{IMAGE} is ready in container {name} on 127.0.0.1:{port}")
        env = dict(os.environ)
        env[URL_ENV] = f"postgres://postgres:{password}@127.0.0.1:{port}/{DATABASE}"
        code = run_command(argv, env)
    except SetupError as e:
        say(f"ERROR: {e}")
        code = 2
    except KeyboardInterrupt:
        say("interrupted")
        code = 130
    finally:
        for sig in stop_signals:
            signal.signal(sig, signal.SIG_IGN)   # nothing interrupts the cleanup
        if started:
            if remove(exe, name):
                say(f"removed container {name}")
            else:
                say(f"ERROR: could not remove container {name}. Remove it by hand: docker rm -f -v {name}")
                code = code or 2
    return code


if __name__ == "__main__":
    sys.exit(main())
