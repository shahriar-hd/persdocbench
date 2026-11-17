#!/usr/bin/env bash
# =============================================================================
# Fix: pi-scholar's research config validator is not idempotent.
#
# Symptom
#   Any pi-scholar config containing a `research` section fails with:
#     Invalid research.providers.arxiv field: apiKey
#   even though the user's file has no apiKey on arxiv.
#
# Cause (upstream bug, @luffysolution/pi-scholar)
#   validateResearchConfig() unconditionally assigns `merged.apiKey` for every
#   provider, leaving the property present with value `undefined` on providers
#   that do not support API keys (arxiv, crossref, unpaywall). The loader
#   validates the research section twice (dist/config.js loads it, then
#   dist/research/tools.js calls loadResearchConfig() on the normalized
#   result), and the second pass rejects the key it created itself.
#
# Fix
#   Only set `apiKey` when a non-empty value resolved; otherwise delete it.
#
# The patch is applied to the installed build, so it must be re-applied after
# every `pi` upgrade. Re-running this script is safe (idempotent).
# =============================================================================
set -euo pipefail

TARGET="${1:-$HOME/.pi/agent/npm/node_modules/@luffysolution/pi-scholar/dist/research/config.js}"

if [[ ! -f "$TARGET" ]]; then
  echo "apply-pi-scholar-idempotency-fix: not found: $TARGET" >&2
  exit 1
fi

if grep -q 'typeof resolved === "string" && resolved.trim() !== ""' "$TARGET"; then
  echo "already patched: $TARGET"
  exit 0
fi

cp "$TARGET" "$TARGET.orig"

python3 - "$TARGET" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
src = path.read_text()

old = '''        merged.apiKey = resolveApiKey(merged, env, id === "semantic-scholar" ? ["SEMANTIC_SCHOLAR_API_KEY"] : id === "openalex" ? ["OPENALEX_API_KEY"] : id === "pubmed" ? ["NCBI_API_KEY"] : id === "easyscholar" ? ["EASYSCHOLAR_SECRET_KEY"] : []);'''

new = '''        {
            const resolved = resolveApiKey(merged, env, id === "semantic-scholar" ? ["SEMANTIC_SCHOLAR_API_KEY"] : id === "openalex" ? ["OPENALEX_API_KEY"] : id === "pubmed" ? ["NCBI_API_KEY"] : id === "easyscholar" ? ["EASYSCHOLAR_SECRET_KEY"] : []);
            if (typeof resolved === "string" && resolved.trim() !== "") merged.apiKey = resolved;
            else delete merged.apiKey;
        }'''

if old not in src:
    sys.exit("apply-pi-scholar-idempotency-fix: pattern not found "
             "(upstream may have fixed it; check before patching)")

path.write_text(src.replace(old, new, 1))
print(f"patched: {path}")
PY

echo "backup: $TARGET.orig"
echo "Restart Pi for the patched module to take effect (Node caches loaded modules)."
