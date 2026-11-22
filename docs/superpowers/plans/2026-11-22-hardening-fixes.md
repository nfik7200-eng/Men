# Hardening Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining offline-fixable hardening gaps (EIP-55 address checksums, silently ignored misspelled `BIP_*` variables, stale "open findings" in the state docs) and re-measure every check after the 2026-11-21 merge repair.

**Architecture:** A stdlib-only Keccak-256 lives in `bip/core/keccak.py` and is validated against `hashlib.sha3_256`, which uses the same permutation and rate and differs only in the padding byte. `evm_address` uses it to reject mixed-case addresses with a wrong checksum, while storage stays lower-case. `load_settings` refuses unknown top-level `BIP_*` variables and names them without their values. The state docs are corrected against the 2026-11-14 log entry.

**Tech Stack:** Python 3.12+, uv, pydantic / pydantic-settings, pytest, ruff, pyright (strict on `bip/core`).

**Spec:** No feature spec. The requirements come from `CURRENT_STATE.md` ("Known limitations": *EIP-55 checksums are not verified*; *Unknown `BIP_*` environment variables are ignored*) and §5 of `CLAUDE.md`. The log entry `IMPLEMENTATION_LOG.md` "2026-11-14 — Stage 3 close-out" shows that M1–M5 were fixed.

## Global Constraints

- No new runtime or dev dependency (no pycryptodome, eth-hash or pysha3). Stdlib only.
- No live network calls. No provider quota is spent.
- Pyright strict must stay clean for `bip/core`; `ruff check .` and `ruff format --check .` must stay clean.
- Stored and emitted EVM addresses stay lower-case. The checksum is validated on input only and never written.
- Error messages list variable names only, never values (`load_settings` docstring rule).
- Commit messages are imperative and explain why. No model identifiers. End each one with the `Co-Authored-By` line from the session.
- Push only to `origin/claude/modest-fermi-0yklvj`. Never force push.
- New architectural choices go into `DECISIONS.md` as ADR-053 with status `Proposed`.

## Review Focus

1. **Pasted checksummed addresses** from explorers (e.g. `0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed`) must still be accepted and lower-cased → Task 2, `test_valid_checksums_are_accepted_and_lowercased`.
2. **All-upper-case hex body** (`0x52908400098527886E0F7030069857D2E4169EE7`) carries no checksum and must be accepted → Task 2, `test_single_case_addresses_skip_the_checksum`.
3. **Existing fixtures with mixed-case, wrong-checksum addresses** would now fail. Fix the fixture data by lower-casing it, not the validator → Task 2, Step 6.
4. **Operator tool variables** (`BIP_PROVIDER_*_URL`, `BIP_LIVE_RPC`, `BIP_NEW_PASSWORD_FILE`, the configured `ai.api_key_env`) must not be refused as unknown → Task 3, `test_tool_variables_are_not_refused`.
5. **Single-underscore typo of a nested setting** (`BIP_AUTH_MODE=required`) must be refused instead of silently leaving auth disabled → Task 3, `test_single_underscore_typo_is_refused`.

---

### Task 1: Stdlib Keccak-256

**Files:**
- Create: `bip/core/keccak.py`
- Test: `tests/unit/test_keccak.py`

**Interfaces:**
- Produces: `keccak256(data: bytes) -> bytes` (32 bytes) and the private `_sponge(data: bytes, pad: int) -> bytes`. Tests import `_sponge`; the leading underscore keeps it out of the public API.

- [ ] **Step 0: Record the baseline before any change**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q 2>&1 | tail -3`
Expected: all clean. Write the pass/fail counts down for the Task 4 log entry. If the baseline is already red, stop and report. Do not fix unrelated failures in this plan.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib
import pytest
from bip.core.keccak import _sponge, keccak256
from bip.chains.ethereum.parse import TRANSFER_TOPIC


def test_empty_input() -> None:
    assert (
        keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_matches_the_transfer_topic_constant() -> None:
    assert "0x" + keccak256(b"Transfer(address,address,uint256)").hex() == TRANSFER_TOPIC


def test_known_sentence() -> None:
    assert keccak256(b"The quick brown fox jumps over the lazy dog").hex() == (
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15"
    )


@pytest.mark.parametrize("n", [0, 1, 135, 136, 137, 271, 272, 1000])
def test_permutation_and_absorption_match_stdlib_sha3(n: int) -> None:
    data = bytes(range(256)) * 4
    assert _sponge(data[:n], 0x06) == hashlib.sha3_256(data[:n]).digest()


def test_differs_from_sha3() -> None:
    assert keccak256(b"") != hashlib.sha3_256(b"").digest()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_keccak.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bip.core.keccak'`

- [ ] **Step 3: Implement `_sponge(data: bytes, pad: int) -> bytes` and `keccak256(data: bytes) -> bytes` in `bip/core/keccak.py`**

Use Keccak-f[1600] over a 25-lane list of 64-bit ints: 24 rounds of θ, ρ, π, χ and ι with the standard round constants and rotation offsets. Rate is 136 bytes and capacity is 512 bits. Absorb lanes little-endian. Padding: append `pad`, zero-fill to a multiple of 136 and OR `0x80` into the last byte (`pad` and `0x80` share one byte when only one pad byte fits). Squeeze the first 32 bytes. `keccak256(data)` returns `_sponge(data, 0x01)`. The module docstring explains why `hashlib.sha3_256` cannot be used (padding byte `0x06` vs Keccak's `0x01`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_keccak.py -v && uv run pyright bip/core/keccak.py`
Expected: 12 passed; pyright 0 errors.

- [ ] **Step 5: Commit**

```bash
git add bip/core/keccak.py tests/unit/test_keccak.py
git commit -m "Add a stdlib Keccak-256 checked against hashlib's SHA3 permutation so EVM checksums can be verified without a new dependency"
```

### Task 2: EIP-55 checksum validation on EVM addresses

**Files:**
- Modify: `bip/contracts/identifiers.py` (the module docstring and `evm_address`, lines 1–7 and 47–50)
- Test: `tests/unit/test_blockchain_contracts.py` (append)

**Interfaces:**
- Consumes: `keccak256(data: bytes) -> bytes` from Task 1.
- Produces: `evm_address(value: str) -> str`, which has the same signature and now raises `ValueError("EIP-55 checksum mismatch")` for mixed-case input with a wrong checksum. `EvmAddress` and every caller (`bip/contracts/blockchain.py:144`) are unchanged.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from bip.contracts.identifiers import evm_address

EIP55_VALID = [
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


@pytest.mark.parametrize("addr", EIP55_VALID)
def test_valid_checksums_are_accepted_and_lowercased(addr: str) -> None:
    assert evm_address(addr) == addr.lower()


@pytest.mark.parametrize(
    "addr",
    [
        "0x52908400098527886E0F7030069857D2E4169EE7",
        "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
        "0xde709f2102306220921060314715629080e2fb77",
        "0x27b1fdb04752bbc536007a920d24acb045561c26",
    ],
)
def test_single_case_addresses_skip_the_checksum(addr: str) -> None:
    assert evm_address(addr) == addr.lower()


def test_wrong_checksum_is_rejected() -> None:
    with pytest.raises(ValueError, match="EIP-55"):
        evm_address("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD")  # last letter case flipped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_blockchain_contracts.py -k "checksum or single_case" -v`
Expected: `test_wrong_checksum_is_rejected` FAILS (DID NOT RAISE); the others pass.

- [ ] **Step 3: Implement the checksum check in `evm_address`**

After the existing regex check, look at the 40-character body. If it is all lower-case or all upper-case, return it lower-cased. Otherwise compute `h = keccak256(body.lower().encode("ascii")).hex()`. Hex digit `i` must be upper-case when `int(h[i], 16) >= 8` and lower-case otherwise (digits are exempt), or raise `ValueError("EIP-55 checksum mismatch")`. Update the module docstring: checksums are now verified on mixed-case input, single-case input carries no checksum, and output stays lower-case.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_blockchain_contracts.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q 2>&1 | tail -15`
Expected: the same pass count as the Task 1 baseline plus the new tests.

- [ ] **Step 6: Repair fixtures that held invalid mixed-case addresses (only if Step 5 shows them)**

For each failure caused by `EIP-55 checksum mismatch`, lower-case the address literal in the fixture or test file (candidates: `tests/stage2/fakes.py`, `tests/stage2/test_parsers.py`, `tests/collector/*.py`). Do not weaken the validator. Re-run Step 5 until it is green, and list every file touched in the commit message.

- [ ] **Step 7: Commit**

```bash
git add bip/contracts/identifiers.py tests/
git commit -m "Reject mixed-case EVM addresses with a wrong EIP-55 checksum so mistyped addresses are caught at input"
```

### Task 3: Refuse unknown top-level `BIP_*` variables

**Files:**
- Modify: `bip/config/settings.py` (add `unknown_bip_variables`; call it in `load_settings`, lines 724–735)
- Modify: `DECISIONS.md` (append ADR-053, Proposed)
- Test: `tests/unit/test_settings.py` (append)

**Interfaces:**
- Produces: `unknown_bip_variables(environ: Mapping[str, str], ai_key_env: str) -> list[str]`, which returns the sorted names that are neither a `Settings` field nor an allowed tool variable. `load_settings(**overrides)` raises `ConfigurationError` that names them.

Allowed names (decided here, recorded in ADR-053):
- The top-level segment (the part after `BIP_` and before the first `__`, lower-cased) is in `Settings.model_fields`.
- The name starts with `BIP_PROVIDER_` or `BIP_LIVE_`.
- The name is exactly `BIP_NEW_PASSWORD` or `BIP_NEW_PASSWORD_FILE`.
- The name equals `ai_key_env` (default `BIP_AI_API_KEY`).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from bip.config.settings import load_settings, unknown_bip_variables
from bip.core.exceptions import ConfigurationError


def test_misspelled_top_level_variable_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_DATAROOT", "/tmp/x")
    with pytest.raises(ConfigurationError, match="BIP_DATAROOT"):
        load_settings()


def test_single_underscore_typo_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_AUTH_MODE", "required")
    with pytest.raises(ConfigurationError, match="BIP_AUTH_MODE"):
        load_settings()


def test_unknown_nested_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_AUTH__MODEX", "required")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_error_never_contains_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIP_DATAROOT", "s3cr3t-value")
    with pytest.raises(ConfigurationError) as exc:
        load_settings()
    assert "s3cr3t-value" not in str(exc.value)


def test_tool_variables_are_not_refused() -> None:
    env = {
        "BIP_PROVIDER_ALCHEMY_ETH_URL": "x",
        "BIP_LIVE_RPC": "1",
        "BIP_NEW_PASSWORD_FILE": "f",
        "BIP_AI_API_KEY": "k",
        "BIP_DATA_ROOT": "/d",
        "BIP_AUTH__MODE": "disabled",
        "PATH": "/bin",
    }
    assert unknown_bip_variables(env, "BIP_AI_API_KEY") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_settings.py -v -k "refused or value"`
Expected: FAIL with `ImportError: cannot import name 'unknown_bip_variables'`. `test_unknown_nested_key_is_refused` may already pass because of `extra="forbid"` on nested models. Note this in the log either way.

- [ ] **Step 3: Implement `unknown_bip_variables` and call it in `load_settings`**

Use a case-insensitive prefix match on `BIP_`, the same as pydantic-settings. In `load_settings`, after `Settings(**overrides)` succeeds, call `unknown_bip_variables(os.environ, settings.ai.api_key_env)`. If the result is not empty, raise `ConfigurationError(f"unknown BIP_ variables (check spelling): {', '.join(names)}")`. If the nested test failed in Step 2, extend the check to refuse unknown keys under `__` for nested model fields.

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run pytest tests/unit/test_settings.py -v && uv run pytest -q 2>&1 | tail -5`
Expected: all green. `tests/conftest.py:54` already clears developer `BIP_*` variables, so hermetic tests are unaffected. If a test sets an unlisted tool variable, add it to the allowlist only if code reads it (`grep -rn '"BIP_' bip`). Otherwise fix the test.

- [ ] **Step 5: Append ADR-053 to `DECISIONS.md`**

Title: "Refuse unknown BIP_ environment variables and verify EIP-55 checksums on input (Proposed; hardening, 2026-11-22)". Context: the two known limitations. Decision: the allowlist above, the stdlib Keccak validated against SHA3, lower-case storage. Consequence: an operator with a misspelled variable gets a startup error naming it.

- [ ] **Step 6: Commit**

```bash
git add bip/config/settings.py tests/unit/test_settings.py DECISIONS.md
git commit -m "Refuse unknown BIP_ variables at startup so a misspelled setting can no longer fall back to its default silently"
```

### Task 4: Correct the state docs, re-measure every check, push

**Files:**
- Modify: `CURRENT_STATE.md` (the "Open (not in the approved scope)" list at lines 57–63; "Known limitations" lines for EIP-55 and unknown variables; the header date)
- Modify: `IMPLEMENTATION_LOG.md` (new entry at the top)
- Modify: `docs/IMPLEMENTATION_ROADMAP.md` only if a status cell says M1–M5 are open

**Interfaces:** none (documentation and evidence).

- [ ] **Step 1: Confirm that M1–M5 are fixed by naming their tests**

Run: `uv run pytest tests/security/test_http.py -k "local_mode or allowed_hosts or foreign_origin" -q 2>&1 | tail -3`, then `uv run pytest tests/discovery/test_pipeline.py -q 2>&1 | tail -3`, then `grep -rn "discovery_lock\|analysis_lock" bip/api/routes/entities.py bip/api/routes/discovery.py | head`
Expected: tests pass and the locks exist. These map to M1 (ADR-048), M4 (relationships rebuild and opportunity refresh locks), and M2, M3 and M5 (`IMPLEMENTATION_LOG.md` 2026-11-14). If any check fails, keep that item listed as open.

- [ ] **Step 2: Run the full backend and frontend checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q 2>&1 | tail -3`
Run: `cd frontend && npm run lint && npm run typecheck && npm test 2>&1 | tail -5`
Expected: all clean. Record the exact counts. If `npm` dependencies are missing, record that it was not run. Do not install without the owner's say-so (`CLAUDE.md` §3).

- [ ] **Step 3: Check CI status**

Run: `gh run list --branch claude/modest-fermi-0yklvj --limit 3`
Expected: a list of runs. If `gh` is unauthenticated or unavailable, record "CI not checked: gh unavailable" and do not claim CI is green.

- [ ] **Step 4: Edit `CURRENT_STATE.md`**

Replace the M1–M6 open list with: M1–M5 fixed 2026-11-14 (tests named in Step 1); M6 (Solana has fewer detectors and memory sources) still open. Replace the two known-limitation lines with the new behaviour (Tasks 2 and 3). Update the "Last updated" header to 2026-11-22 and the test totals with the Step 2 counts.

- [ ] **Step 5: Prepend the `IMPLEMENTATION_LOG.md` entry**

Heading `## 2026-11-22 — Hardening fixes`. Include: the baseline counts (Task 1 Step 0), each task's red-then-green evidence, the fixtures touched in Task 2 Step 6, the nested-key finding from Task 3 Step 2, the Step 2 counts and the CI result from Step 3.

- [ ] **Step 6: Commit and push**

```bash
git status && git diff --stat
git add CURRENT_STATE.md IMPLEMENTATION_LOG.md docs/IMPLEMENTATION_ROADMAP.md
git commit -m "Record the hardening fixes and correct the state docs, which still listed M1 to M5 as open after their 2026-11-14 fixes"
git fetch origin claude/modest-fermi-0yklvj && git status -sb | head -1
git push origin claude/modest-fermi-0yklvj
```
Expected: the branch is not behind before the push (if it is, stop and reconcile per §8 of `CLAUDE.md`); the push succeeds. Report each commit hash.
