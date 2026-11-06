#!/usr/bin/env bash
# Inspect the tag and GitHub Release before choosing the next publication step.
set -euo pipefail
tag=$1
ref="refs/tags/$tag"
refs=$(git ls-remote --tags origin "$ref" "${ref}^{}")
if [[ -n "$refs" ]]; then
  # For an annotated tag, compare the peeled commit to the release source.
  commit=$(awk -v ref="$ref" '
    $2 == ref {tag=$1}
    $2 == ref "^{}" {peeled=$1}
    END {print peeled ? peeled : tag}
  ' <<<"$refs")
  if [[ "$commit" != "$2" ]]; then
    echo "$ref points to $commit. Expected $2." >&2
    exit 1
  fi
fi

release=$(gh api --paginate --slurp "repos/$GITHUB_REPOSITORY/releases?per_page=100" |
  jq -r --arg tag "$tag" 'first(.[][] | select(.tag_name == $tag) |
    if .draft then "draft" elif .prerelease then error("unexpected prerelease")
    else "published" end) // "missing"')
if [[ -z "$refs" ]]; then
  if [[ "$release" != missing ]]; then
    echo "Release $tag has no matching Git tag." >&2
    exit 1
  fi
  echo untagged
else
  echo "$release"
fi
