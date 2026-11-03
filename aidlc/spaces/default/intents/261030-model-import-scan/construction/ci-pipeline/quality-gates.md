# Quality Gates

Stage: CI Pipeline (`ci-pipeline`), scope `mvp`. Date: 2026-11-02.

A quality gate is a check whose failure stops a change from being deployed. The answer to Q3 of `ci-pipeline-questions.md` is that every check below blocks. How the pipeline runs them is in `ci-config.md`. The commands and figures come from what Build and Test ran by hand on this machine (`construction/build-and-test/build-and-test-summary.md` and `construction/build-and-test/test-results.md`) and from the code summaries of the two Units (`construction/u1-import-slice/code-generation/code-summary.md`, `construction/u2-import-rules/code-generation/code-summary.md`). No gate has run in a pipeline yet.

## Gates

| # | Gate | Command | Passes when | Requirement | Local evidence from Build and Test |
|---|------|---------|-------------|-------------|------------------------------------|
| G1 | Dependencies consistent | `pip check` | Exit 0 | NFR6.2 | Run R1: no broken requirements |
| G2 | Formatting | `python -m black --check src infra tests app.py` | Exit 0, no file would change | NFR6.2 | Run R2: 44 files unchanged |
| G3 | Lint | `python -m ruff check src infra tests app.py` | Exit 0 | NFR6.2 | Run R3: all checks passed |
| G4 | Tests and coverage | `python -m pytest --cov --cov-fail-under=80` | All tests pass and line coverage is at least 80 percent for the whole project | NFR6.1, NFR6.3 to NFR6.8, the Testing Contract of both Units | Run R6: 540 passed, 95.88 percent |
| G5 | Dependency vulnerabilities | `pip-audit --local` | No known vulnerability in the installed packages | NFR3.12 | Run R7: 12 findings, all in `pip` 24.3.1 of the local environment, none in the pinned project packages. Not clean as run; see below |
| G6 | Secret scan | `gitleaks detect --no-git --source .` | No finding | NFR3.1 | Not run: no scan tool existed. Unverified in Build and Test |
| G7 | Stack synthesis and template tests | `python app.py` and the 70 CDK template tests inside G4 | Synthesis exits 0; no `s3:*` and no `Resource: *` in the task role; bucket settings, lifecycle rules, alarms as designed | NFR3.4 to NFR3.7, NFR4.9, NFR5.17 to NFR5.20 | Run R4: exit 0; the template tests passed in R5 and R6 |
| G8 | Image scan | `docker build -t model-import:ci .` then `trivy image --severity CRITICAL --exit-code 1 model-import:ci` | The image builds and has no critical vulnerability | NFR3.12 | Not run: Docker is not installed on this machine. Unverified in Build and Test |
| G9 | Readiness | The readiness script of `ci-config.md` | The settings, break-glass roles, SES identity and sandbox status, and mailbox are in order | NFR3.16 | Not run: needs the real account. Unverified in Build and Test |
| G10 | Deployment | `cdk deploy` | The stack update succeeds, or CloudFormation rolls it back | | Not run |
| G11 | Smoke check | The smoke-check script of `ci-config.md` | The test model is stored with a valid `_COMPLETE` marker and the holding bucket is empty, within 90 minutes | NFR1.2 to NFR1.5 (first real figures), NFR4.13 (the marker) | Not run: needs a deployed stack and a real download |

G1 to G8 run before anything is deployed. G9 to G11 run only when the prerequisites 3 to 7 of `ci-config.md` are met; until then the pipeline runs G1 to G8 and stops.

## What the gates enforce from Build and Test

The commands Build and Test recorded are the ones G1 to G4 and G7 run, with the same options, so the pipeline enforces the Construction Verification Command of both Units: `python -m pytest --cov --cov-fail-under=80`. The coverage floor of 80 percent is not lowered by any gate. The CI obligation of the Testing Contract, "Run the selected tests in CI before merge", is met once this pipeline exists and G4 runs on every push to the main branch; it was recorded as `N/A` for this stage ahead in Build and Test (target `TC-CI-OBLIGATION`).

## Targets Build and Test left to CI

Build and Test ended with 22 targets that were not `Met` (3 `Not Met`, 19 `Unverified`) and that the person accepted. A pipeline can close only some of them, and only after it exists and has run:

| Target (Build and Test) | Closed by | Note |
|-------------------------|-----------|------|
| `NFR3.1-CI-SECRET-SCAN` | G6 | Needs the pipeline to exist and the scan to run clean |
| `TC-u1-IMAGE-BUILD` | G8 | Needs Docker in the build project |
| `NFR3.16` (SES sandbox and identities) | G9 | Needs the real account |
| `NFR1.2` to `NFR1.5`, `NFR1.10`, `NFR2.1-STAGED-RUN`, `NFR2.2` (real import figures) | G11 in part | One smoke import gives first real figures for a small model only; a staged run of a model of 10 GB or more is still needed for the 20 GB figures |
| `NFR6.8` (u1 tests unchanged except where a rule requires it) | Not a gate | Needs Git history; Git is not installed, so the baseline is lost until a repository exists |
| The tests missing for `NFR3.24`, `NFR5.3-CAP`, `NFR1.11`, `NFR1.12`, `NFR1.13`, `NFR2.7`, `NFR2.8`, `ND1-PERF-IO`, and `worker.py` and `store.py` below 90 percent | Not a gate | The person chose not to add these tests at Build and Test |
| `ND1-OBS-STUCK` (alarm at 10 hours, not 8) and `ND1-SEC-LIST-PREFIX` (listing of whole buckets) | Not a gate | Differences chosen at Infrastructure Design; the person accepted the failure at Build and Test |

## Gate rules

- A gate that cannot run (a tool is missing, the build project fails to start) is a failure, not a pass.
- No gate is switched off to let a change through. A gate that is wrong is fixed in the pipeline definition and reviewed like code.
- The coverage threshold and the Black, Ruff and coverage settings live in `pyproject.toml` and are not overridden on the pipeline's command line.
- A scan finding is shown in the build log without the matched value: the Gitleaks run must not print a secret it finds (no `--verbose` output of matches in a shared log). This follows the rule that no secret, token or credential appears in code or logs (`aidlc/spaces/default/memory/project.md`, Forbidden).

## Open points

- G5 is not clean as run locally. The 12 findings are in `pip` 24.3.1 of the local environment; the pipeline upgrades `pip` first, but whether the gate then passes is only known after a first run. If it fails on a package the project pins, the pin changes (a code change) or a documented, dated exception is added with the person's approval.
- The Gitleaks option form and the Trivy option form are written from memory of the tools and must be checked against the pinned versions when the build project is created.

## Assumptions & Open Questions

- [assumption] Gitleaks and Trivy run inside the build project from pinned releases and need no service in the account.
- [assumption] The 80 percent coverage floor applies to the whole project, as the Testing Contract of both Units says.
- [Q1] Is a documented exception acceptable for a `pip-audit` finding in a package that has no fixed release? None is defined here.
