"""The deployables contract: the REPO is the source, the game dir is a copy target.

This is what keeps the MCP reviewable - every blueprint it authors is a git-tracked file -
and it is why the server does not author inside the game tree.
"""
import asyncio
import os
import pathlib
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import main as M                                              # noqa: E402
from tests.test_worldmodel import live_game                 # noqa: E402


class Req:
    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


def test_deployables_tree_exists_and_is_not_ignored():
    root = pathlib.Path(__file__).resolve().parents[1]
    bp = root / "deployables" / "blueprints"
    sc = root / "deployables" / "scripts"
    # Every deployable dir must ship a tracked file, because git does not track empty
    # directories: an empty deployables/scripts vanished from a fresh clone (found by
    # cloning the repo), so a clone could not deploy scripts at all.
    assert bp.is_dir(), f"missing {bp} (clone-unsafe?)"
    assert sc.is_dir(), f"missing {sc} - an empty dir is NOT cloned; keep a README in it"
    assert list(bp.glob("*.csv")), "no blueprints in deployables/blueprints"
    assert (sc / "README.md").is_file(), "deployables/scripts must ship a README (keeps the dir in git)"
    assert (root / "deployables" / "README.md").is_file()
    assert (root / "scripts" / "deploy.sh").is_file()
    assert os.access(root / "scripts" / "deploy.sh", os.X_OK)
    assert shutil.which("git") is None or subprocess_ok(root)


def subprocess_ok(root):
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", "deployables/blueprints"],
                       cwd=root, capture_output=True)
    return r.returncode != 0            # 0 would mean the source tree is ignored


def test_blueprint_dir_defaults_to_the_repo():
    """Authoring path must be inside the repo, not the game tree."""
    assert "deployables" in M.REPO_BLUEPRINT_DIR
    assert not M.REPO_BLUEPRINT_DIR.startswith("/mnt")


def test_apply_writes_repo_then_copies_to_target():
    if not live_game():
        print("SKIP test_apply_writes_repo_then_copies_to_target (no game)")
        return
    tmp = tempfile.mkdtemp()
    repo, game = os.path.join(tmp, "repo"), os.path.join(tmp, "game")
    os.makedirs(repo); os.makedirs(game)
    old_repo, old_game = M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR
    M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR = repo, game
    try:
        out = asyncio.run(M.apply_blueprint(Req({
            "name": "unit_flow", "mode": "dig",
            "grid": [["d", "d"], ["d", "d"]], "cursor": [100, 100, 165]})))
    finally:
        M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR = old_repo, old_game
    assert out.get("blueprint"), out
    assert os.path.isfile(os.path.join(repo, "unit_flow.csv")), "repo source missing"
    assert os.path.isfile(os.path.join(game, "unit_flow.csv")), "deployed copy missing"
    body = open(os.path.join(repo, "unit_flow.csv")).read()
    assert body.splitlines()[0].startswith("#dig"), body
    # the reply names both locations, so a caller can tell authoring from deployment
    assert out["deployed_to"].startswith(game)
    shutil.rmtree(tmp, ignore_errors=True)


def test_missing_deploy_target_is_reported_not_crashed():
    """No target configured must answer with the deploy instruction, not a 500."""
    tmp = tempfile.mkdtemp()
    repo = os.path.join(tmp, "repo"); os.makedirs(repo)
    old_repo, old_game = M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR
    M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR = repo, ""
    try:
        out = asyncio.run(M.apply_blueprint(Req({
            "name": "no_target", "mode": "dig", "grid": [["d"]], "cursor": [1, 1, 1]})))
    finally:
        M.REPO_BLUEPRINT_DIR, M.DEPLOY_BLUEPRINT_DIR = old_repo, old_game
    assert out.get("success") is False
    assert "no deploy target" in out["error"]
    assert "deploy.sh" in out["hint"]
    assert os.path.isfile(os.path.join(repo, "no_target.csv")), "repo file must still be written"
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
