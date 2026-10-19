# Contributing

Use a virtual environment and `python -m pip install -e .`. Unit tests use the
standard library and can also run directly from the checkout without USB or
third-party Python packages:

```sh
python -m unittest discover -s tests -v
node tests/test_wiring.cjs
```

Browser tests require Node.js 22.12+, Puppeteer, and Chrome:

```sh
npm ci
PYTHON=python npm run test:ui
```

On PowerShell, set `$env:PYTHON = "python"` before `npm run test:ui`.
`CHROME_PATH` can point to an installed browser. `PUPPETEER_MODULE` can point to
an existing Puppeteer module. With no override, Puppeteer uses its downloaded
browser. Tests bind temporary loopback ports and remove their temporary workspaces.
The hardware-mode UI test mocks adapter discovery and starts no hardware job.

The installed application is live-only. Device models and backend injection live
in `tests/`; the wheel includes no simulator. Never add a silent hardware fallback.
Keep model results and bench observations distinct. New profiles need cited
primary references, explicit geometry and exclusions, ID checks, failure-path
tests, and a documented hardware validation status. Do not import proprietary
loaders or upload real firmware/backup files to tests or issues. Generate fixtures
at runtime. Include target, selected range, reset mapping, host OS, Python version,
and redacted errors in bug reports.

Build and release checks are described in [docs/RELEASING.md](docs/RELEASING.md).
