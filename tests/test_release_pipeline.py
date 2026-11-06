from __future__ import annotations

import json
import os
from importlib.metadata import version
from subprocess import (  # ruff: ignore[suspicious-subprocess-import] -- fake command results
    CalledProcessError,
    CompletedProcess,
)
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from yaml import safe_load

from build_scripts import release
from docs.scripts import published_release, versioned_site
from lyra.version import APP_VERSION

if TYPE_CHECKING:
    from pathlib import Path

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
BASE_VERSION = "0.27.0"
ROOT = release.ROOT


def _config() -> str:
    return json.dumps(
        {
            "skip-github-release": True,
            "release-type": "python",
            "include-component-in-tag": False,
            "packages": {
                ".": {
                    "package-name": "lyra",
                    "exclude-paths": ["docs/**"],
                }
            },
        }
    )


def _changelog(package_version: str) -> str:
    return (
        "# Changelog\n\n"
        f"## [{package_version}](https://example.test/compare) (2026-07-21)\n\n"
        "### Bug Fixes\n\n"
        "* Correct the release pipeline\n"
    )


def _repository_files(after: str = "0.27.1") -> dict[tuple[str, str], str]:
    files = {
        (BASE_SHA, release.MANIFEST_PATH): json.dumps({".": BASE_VERSION}),
        (HEAD_SHA, release.MANIFEST_PATH): json.dumps({".": after}),
        (HEAD_SHA, release.CONFIG_PATH): _config(),
    }
    files[HEAD_SHA, "pyproject.toml"] = (
        f'[project]\nname = "lyra"\nversion = "{after}"\n'
    )
    files[HEAD_SHA, "CHANGELOG.md"] = _changelog(after)
    return files


def _patch_repository(
    monkeypatch: pytest.MonkeyPatch, files: dict[tuple[str, str], str]
) -> None:
    def git(_root: Path, command: str, ref: str) -> str:
        if command == "rev-parse":
            assert ref in {BASE_SHA + "^{commit}", HEAD_SHA + "^{commit}"}
            return ref.removesuffix("^{commit}")
        assert command == "show"
        commit, path = ref.split(":", 1)
        return files[commit, path]

    monkeypatch.setattr(release, "_git", git)


def test_current_release_configuration_is_consistent() -> None:
    release.validate_repository()
    assert version("lyra") == APP_VERSION


def test_plan_release_emits_manifest_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repository(monkeypatch, _repository_files())
    github_output = tmp_path / "github-output"

    plan = release.plan_release(
        BASE_SHA,
        HEAD_SHA,
        tmp_path / "release",
        github_output=github_output,
    )

    identity = {"version": "0.27.1", "tag": "v0.27.1", "commit": HEAD_SHA}
    assert (plan.version, plan.tag, plan.commit) == ("0.27.1", "v0.27.1", HEAD_SHA)
    assert json.loads(plan.manifest_path.read_text()) == {
        "schema_version": 2,
        "name": "lyra",
        **identity,
    }
    assert plan.notes_path.stat().st_size > 0
    assert (
        dict(line.split("=", 1) for line in github_output.read_text().splitlines())
        == identity
    )


def test_plan_release_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repository(monkeypatch, _repository_files())

    first = release.plan_release(BASE_SHA, HEAD_SHA, tmp_path / "first")
    second = release.plan_release(BASE_SHA, HEAD_SHA, tmp_path / "second")

    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.notes_path.read_bytes() == second.notes_path.read_bytes()


@pytest.mark.parametrize("after", [BASE_VERSION, "0.26.9"])
def test_plan_release_requires_product_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: str
) -> None:
    _patch_repository(monkeypatch, _repository_files(after))

    with pytest.raises(
        release.ReleasePlanError,
        match="version must increase",
    ):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


def test_plan_release_requires_product_changelog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _repository_files()
    files[HEAD_SHA, "CHANGELOG.md"] = "# Changelog\n"
    _patch_repository(monkeypatch, files)

    with pytest.raises(
        release.ReleasePlanError, match=r"has no release entry for 0\.27\.1"
    ):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        (release.MANIFEST_PATH, '{".": "1.2"}'),
        (release.MANIFEST_PATH, '{".": "01.2.3"}'),
        (release.MANIFEST_PATH, '{".": "1.2.3-rc1"}'),
        (release.MANIFEST_PATH, '{"other": "1.2.3"}'),
        ("pyproject.toml", '[project]\nname="other"\nversion="0.27.1"'),
        ("pyproject.toml", '[project]\nname="lyra"\nversion="0.27.0"'),
        ("CHANGELOG.md", "## 0.27.1\n\n## 0.27.0\nold"),
    ],
)
def test_invalid_metadata_stops_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, content: str
) -> None:
    files = _repository_files()
    files[HEAD_SHA, path] = content
    _patch_repository(monkeypatch, files)
    with pytest.raises(release.ReleasePlanError):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("include-component-in-tag", True),
        ("exclude-paths", ["src/**"]),
        ("changelog-path", "other.md"),
    ],
)
def test_release_configuration_rejects_inconsistent_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: object
) -> None:
    files = _repository_files()
    config = json.loads(_config())
    config["packages"]["."][key] = value
    files[HEAD_SHA, release.CONFIG_PATH] = json.dumps(config)
    _patch_repository(monkeypatch, files)
    with pytest.raises(release.ReleasePlanError):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


def test_release_plan_requires_full_commit_shas(tmp_path: Path) -> None:
    with pytest.raises(
        release.ReleasePlanError, match="must be a full lowercase commit SHA"
    ):
        release.plan_release("HEAD^", HEAD_SHA, tmp_path)


def test_release_plan_rejects_non_commit_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "_git", Mock(return_value="c" * 40))
    with pytest.raises(release.ReleasePlanError, match="identify the commit itself"):
        release.plan_release(BASE_SHA, HEAD_SHA, tmp_path)


def test_release_please_only_manages_the_release_pr() -> None:
    workflow = (ROOT / ".github/workflows/release-please.yml").read_text()

    assert "googleapis/release-please-action@" in workflow
    assert "RELEASE_PLEASE_TOKEN" in workflow
    assert "gh release" not in workflow
    assert "github-builder" not in workflow


def test_publisher_reuses_main_validation() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    publisher = (ROOT / ".github/workflows/publish-release.yml").read_text()
    assert "needs: [detect-release, python, documentation]" in ci
    assert "needs: [detect-release, documentation]" in ci
    assert "uv build --out-dir" in ci
    assert "uv lock --check" in ci
    assert "workflow_call:" in publisher
    assert "pull_request:" not in publisher
    assert "validate-docs:" not in publisher
    assert "uv build" not in publisher
    assert "uv run --no-project --python 3.11" in publisher
    assert "queue: max" in publisher
    assert "needs: finalize" in publisher
    assert "refresh: true" in publisher
    assert ".merge_commit_sha == $sha" in ci
    assert ".head.repo.full_name == $repo" in ci
    assert "autorelease: pending" in ci
    assert "autorelease: tagged" in ci
    assert '"$count" -gt 1' in ci
    jobs = safe_load(publisher)["jobs"]
    assert jobs["plan"]["outputs"].keys() == {"version", "tag", "commit", "state"}
    assert jobs["publish-app-image"]["needs"] == ["plan"]
    assert jobs["publish-app-image"]["if"] == "needs.plan.outputs.state != 'published'"
    assert jobs["publish-github-release"]["needs"] == ["plan", "publish-app-image"]
    assert jobs["finalize"]["needs"] == ["publish-github-release"]


def test_docs_reuse_and_publication_boundaries() -> None:
    validation = (ROOT / ".github/workflows/docs-validate.yml").read_text()
    deployment = (ROOT / ".github/workflows/docs-pages.yml").read_text()
    assert "fetch-depth: 0" not in validation
    assert "npm run ci" in validation
    assert "source-sha.txt" in validation
    assert "source-base.txt" in deployment
    assert "workflow_dispatch:" in deployment
    assert "steps.latest.outputs.reuse" in deployment
    assert "steps.latest.outputs.eligible" in deployment
    assert "needs.build.outputs.eligible" in deployment
    assert "uv.lock" not in deployment
    jobs = safe_load(deployment)["jobs"]
    selectors = [
        next(step for step in jobs[job]["steps"] if step.get("id") == "release")
        for job in ("build", "deploy")
    ]
    assert selectors[0]["run"] == selectors[1]["run"]
    assert "docs.scripts.published_release" in selectors[0]["run"]
    assert "RELEASE_SHA" in deployment
    assert "cache/restore" not in deployment
    assert "--release-sha" in deployment
    assert not (ROOT / ".github/actions/validate-python/action.yml").exists()


@pytest.mark.parametrize("supported", [True, False])
def test_select_published_release(
    monkeypatch: pytest.MonkeyPatch, *, supported: bool
) -> None:
    def record(
        tag: str, *, draft: bool = False, prerelease: bool = False
    ) -> dict[str, object]:
        return {"tag_name": tag, "draft": draft, "prerelease": prerelease}

    pages = [
        [record("lyra-v9.0.0"), record("v8.0.0", draft=True)],
        [record("lyra-sdk-v7.0.0"), record("v6.0.0", prerelease=True)],
    ]
    if supported:
        pages[0].append(record("v1.2.0"))
        pages[1].append(record("v1.10.0"))
    api = Mock(
        side_effect=[
            CompletedProcess([], 0, json.dumps(pages)),
            CompletedProcess([], 0, HEAD_SHA),
        ]
    )
    monkeypatch.setattr(published_release, "run", api)
    assert published_release.published_release("test/repo") == (
        ("v1.10.0", HEAD_SHA) if supported else ("", "")
    )
    assert "--paginate" in api.call_args_list[0].args
    assert api.call_count == (2 if supported else 1)
    if supported:
        assert "repos/test/repo/commits/tags/v1.10.0" in api.call_args.args


def test_release_lookup_failure_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Mock(side_effect=CalledProcessError(1, "gh"))
    monkeypatch.setattr(published_release, "run", api)
    with pytest.raises(CalledProcessError):
        published_release.published_release("test/repo")


def test_empty_release_list_skips_tag_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Mock(return_value=CompletedProcess([], 0, "[[]]"))
    monkeypatch.setattr(published_release, "run", api)
    assert published_release.published_release("test/repo") == ("", "")
    api.assert_called_once()


def test_selected_release_requires_a_resolvable_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [[{"tag_name": "v1.0.0", "draft": False, "prerelease": False}]]
    api = Mock(
        side_effect=[
            CompletedProcess([], 0, json.dumps(pages)),
            CalledProcessError(1, "gh"),
        ]
    )
    monkeypatch.setattr(published_release, "run", api)
    with pytest.raises(CalledProcessError):
        published_release.published_release("test/repo")


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("changed", ["none", "main", "tag", "release_sha"])
def test_docs_freshness_with_fake_commands(
    tmp_path: Path, changed: str, *, released: bool
) -> None:
    workflow = safe_load((ROOT / ".github/workflows/docs-pages.yml").read_text())
    step = next(
        item
        for item in workflow["jobs"]["deploy"]["steps"]
        if item.get("id") == "fresh"
    )
    tag, sha = ("v1.0.0", HEAD_SHA) if released else ("", "")
    environment = dict(
        os.environ,
        GITHUB_REPOSITORY="example/repo",
        GITHUB_OUTPUT=str(tmp_path / "output"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
        SOURCE_SHA=BASE_SHA,
        RELEASE_SHA=sha,
        RELEASE_TAG=tag,
        CURRENT_MAIN=HEAD_SHA if changed == "main" else BASE_SHA,
        CURRENT_TAG="v2.0.0" if changed == "tag" else tag,
        CURRENT_SHA=BASE_SHA if changed == "release_sha" else sha,
    )
    script = """gh() {
      [[ "$*" == "api repos/example/repo/commits/main --jq .sha" ]] || return 1
      echo "$CURRENT_MAIN"
    }
    """ + step["run"]
    versioned_site.run(
        "bash",
        "-e",
        "-o",
        "pipefail",
        "-c",
        script,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
    )
    assert (tmp_path / "output").exists() == (changed == "none")


@pytest.mark.parametrize(
    "case", ["retry", "conflicting-tag", "wrong-plan", "wrong-asset"]
)
def test_publish_release_with_fake_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _patch_repository(monkeypatch, _repository_files())
    plan = release.plan_release(BASE_SHA, HEAD_SHA, tmp_path / "lyra-release")
    workflow = safe_load((ROOT / ".github/workflows/publish-release.yml").read_text())
    publish = workflow["jobs"]["publish-github-release"]["steps"][-1]["run"]
    environment = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        RUNNER_TEMP=str(tmp_path),
        GITHUB_REPOSITORY="example/repo",
        PLANNED_STATE="untagged",
        TAG=plan.tag,
        VERSION=plan.version,
        RELEASE_SHA=plan.commit,
    )
    git = tmp_path / "git"
    git.write_text("""#!/usr/bin/env bash
set -eu
case "$1" in
  ls-remote) if [[ -f "$RUNNER_TEMP/tag" ]]; then cat "$RUNNER_TEMP/tag"; fi ;;
  push) [[ "$*" == "push origin ${RELEASE_SHA}:refs/tags/${TAG}" ]]
        [[ ! -f "$RUNNER_TEMP/tag" ]]
        echo "$RELEASE_SHA refs/tags/$TAG" > "$RUNNER_TEMP/tag" ;;
  *) exit 2 ;;
esac
""")
    gh = tmp_path / "gh"
    gh.write_text("""#!/usr/bin/env bash
set -eu
cd "$RUNNER_TEMP"
if [[ "$1" == api ]]; then
  if [[ -f state ]]; then
    jq --arg tag "$TAG" '
      [[{tag_name:$tag, draft:.isDraft, prerelease:.isPrerelease}]]
    ' state
  else
    echo '[[]]'
  fi
  exit 0
fi
[[ "$1" == "release" && "$3" == "$TAG" ]]
case "$2" in
  create) [[ ! -f state ]]; echo '{"isDraft":true}' > state ;;
  upload) if [[ -f fail-upload ]]; then exit 1; fi
          cp "$4" asset ;;
  edit) [[ -f asset ]]; echo '{"isDraft":false,"isPrerelease":false}' > state ;;
  download) cp asset "${!#}" ;;
  *) exit 2 ;;
esac
""")
    git.chmod(0o755)
    gh.chmod(0o755)

    def publish_once() -> None:
        versioned_site.run(
            "bash",
            "-e",
            "-o",
            "pipefail",
            "-c",
            publish,
            cwd=ROOT,
            env=environment,
            capture_output=True,
        )

    if case == "conflicting-tag":
        (tmp_path / "tag").write_text(f"{BASE_SHA} refs/tags/{plan.tag}")
    elif case == "wrong-plan":
        environment["RELEASE_SHA"] = BASE_SHA
    elif case == "wrong-asset":
        (tmp_path / "tag").write_text(f"{HEAD_SHA} refs/tags/{plan.tag}")
        (tmp_path / "state").write_text('{"isDraft":false,"isPrerelease":false}')
        (tmp_path / "asset").write_text("{}")
    else:
        (tmp_path / "fail-upload").touch()

    with pytest.raises(CalledProcessError):
        publish_once()
    if case == "retry":
        assert json.loads((tmp_path / "state").read_text())["isDraft"]
        (tmp_path / "fail-upload").unlink()
        publish_once()
        assert not json.loads((tmp_path / "state").read_text())["isDraft"]
        environment["PLANNED_STATE"] = "published"
        publish_once()
        assert (tmp_path / "asset").read_bytes() == plan.manifest_path.read_bytes()
    elif case == "wrong-asset":
        assert (tmp_path / "asset").read_text() == "{}"
    else:
        assert not (tmp_path / "state").exists()
