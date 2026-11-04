# Contributing

## Setup and checks

Follow "Verify" in `README.md`. Before a pull request, run from the repository root:

```bash
export PYTHONPATH=service-kit/vendor
ruff check src tests scripts service-kit
failed=0
for gate in service-kit/gates/check_*.py; do
  python "$gate" || [ "$?" -eq 3 ] || { echo "FAILED: $gate"; failed=1; }
done
[ "$failed" -eq 0 ]                       # exit 3 is a named skip; any other non-zero is red
pytest tests
bash scripts/check_service_iac.sh      # needs terraform or tofu on PATH
```

The tests use real PostgreSQL, Redis and AWS endpoints started through Docker Compose,
not mocks. A skipped test is not a pass: CI sets `REQUIRE_REAL_RESOURCES=1`, so fix the
missing resource instead of opening a pull request on a skip.

## Rules

- Keep the seven layers pointing one way; the gates in `service-kit/gates/` enforce
  the service module contract described in `README.md`.
- Changes to `service-kit/` reach every emitted service through `service-kit/sync.py`.
  Keep them generic: no product names, account IDs, hostnames or per-product code paths.
- After changing `requirements/*.in`, regenerate the matching lock with
  `pip-compile --generate-hashes`; never edit a lock by hand.
- Do not commit credentials, `.env` files, local databases or logs containing personal
  data. Examples use reserved names such as `example.com` and `example.invalid`.

## License

By contributing you agree that your contribution is licensed under the MIT License in
`LICENSE`.
