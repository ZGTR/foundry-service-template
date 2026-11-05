# GATE_MUST_FLAG_THIS -- this folder carries exactly two clause violations:
#   * entrypoint.sh has a stranded `exec ... "$@"` after an unconditional exec
#     that never returns, so every container CMD is silently discarded; and
#   * docker/README.md is absent, so the shared folder is incomplete.
# `wait-for.sh` and the three compose files are CORRECT here, deliberately:
# their silence is what makes the two findings above measurements of their own
# clauses rather than of "something in this folder is wrong".
FROM python:3.12-slim
ENTRYPOINT ["/srv/entrypoint.sh"]
