# Building and publishing

The repository is source-first: wheel and source archive downloads run locally.
It is not a GitHub Pages deployment, hosted USB service, or native installer.

## Local checks

```sh
python -m pip install -e . build twine
python -m unittest discover -s tests -v
npm ci
PYTHON=python npm test
PYTHON=python npm run test:ui
python tools/check_release.py
python -m build
python -m twine check dist/*
python tools/check_release.py --dist dist
python tools/smoke_wheel.py
```

`check_release.py` checks the Git index (or the public source allowlist before
Git initialization), relative documentation links, packaged UI assets, licenses,
and common private file/content patterns. It is a scoped release check, not a
guarantee that arbitrary future contributions contain no secrets. Inspect staged
changes before publishing. `smoke_wheel.py` installs the built wheel without
dependencies into a temporary directory and checks its CLI and every served UI
asset using a temporary loopback server; it never opens USB.

## First GitHub push

Create an empty repository under the intended account, with no generated README.
From this standalone repository (not any parent working directory):

```sh
git remote add origin https://github.com/OWNER/tigard-flasher.git
git push -u origin main
```

Replace `OWNER` with the destination account. The preparation step does not
create a remote or push anything. Enable private vulnerability reporting in the
repository settings if desired. After push, confirm all CI jobs pass. Hosted
Windows/Linux jobs have not run merely because their workflow exists.

## Versioned release

Update `spc5tool/__init__.py`, the changelog, and validation notes. Review the diff,
run all checks, commit, then tag the intended commit (for example `v0.4.0`).
Pushing a `v*` tag runs the CI jobs and packages wheel/sdist artifacts; it does
not publish to PyPI or create a public GitHub Release automatically. Download
the successful CI artifact or use the locally checked `dist/` files, then create
a GitHub Release with those files and the limitations from the validation record.

For a source ZIP with CI files included:

```sh
git archive --format=zip --prefix=tigard-flasher/ --output=../tigard-flasher-source.zip HEAD
```

The local UI workspace, firmware, vendor inputs, private bench records,
dependencies, and generated test results must stay out of the repository.
