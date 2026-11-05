# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately to mohammadshakergtr@gmail.com with
reproduction steps, the affected commit and the impact. Do not open a public issue,
and do not include credentials or personal data. This project does not promise a
response-time SLA. Fixes land on `main`; there is no long-term support branch.

Services emitted by `scripts/new_service.sh` are separate repositories. Report a flaw
in an emitted service to its own maintainers, and report it here as well when the cause
is code the template ships.

## Threat model

| Asset | Threat | Control |
|---|---|---|
| Caller identity | a caller asserts an org, subject or scope it was not issued | outside `SERVICE_ENV=local`/`test` the service refuses to start on unverified header auth; `adapters/real/jwt_principal_auth.py` takes the principal from signed claims only, with an algorithm allow-list, `kid`-selected keys, and required issuer and audience |
| Deployment stage | a deployment that forgot `SERVICE_ENV` silently runs as `local` | `SERVICE_ENV` has no default; the config refuses to start without it |
| Network exposure | the service becomes reachable from the internet by accident | neither deploy target creates a load balancer or public API; `tests/shape/test_private_ingress.py` refuses an internet-facing load balancer, an ingress rule from `0.0.0.0/0` or `::/0`, a task with a public IP, and an HTTP API event unless the service declares `exposure` as `public` |
| Database credentials | leaked through logs or the image | read at startup from the environment or AWS Secrets Manager; no secret is baked into the image |
| Supply chain | a dependency or base image changes under a green build | base images pinned by digest; runtime and test dependencies installed with `pip install --require-hashes` from `requirements/*.lock` |
| Container runtime | a compromised process rewrites application code | numeric non-root user (`65532`) and a read-only application tree |

Out of scope: denial of service at the network edge, the identity provider that issues
tokens, and the AWS account an emitted service deploys into.
