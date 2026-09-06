# AGENTS.md

DarkAngel — **Python 3.14**, piloté par `uv` (single package `darkangel`,
`src/darkangel/`).

## Architecture

- **Backend** — `src/darkangel/{api.py,main.py}` : service FastAPI
  (`/`, `/health`, OpenAPI).
- **Frontend (GUI)** — `src/darkangel/gui/` : application de bureau PySide6.
  - `client.py` : `ApiClient` (httpx) vers le backend.
  - `window.py` : `build_window()` — `QMainWindow`, requêtes exécutées dans un
    `QThread` (l'UI ne bloque pas).
  - `main.py` : point d'entrée `darkangel-gui`.

Le frontend ne parle pas à FastAPI directement : tout passe par `ApiClient`.
Le backend est un **process externe** que le GUI ne démarre pas.

## Prérequis du GUI

- Python 3.14 (voir `.python-version`).
- Sous Linux/CI **headless** : `QT_QPA_PLATFORM=offscreen` (déjà positionné
  par `tests/conftest.py` pour les tests).

## Commandes

```
uv run pytest            # suite complète (unit + intégration + régression + GUI)
uv run darkangel         # backend FastAPI (defaut http://127.0.0.1:8000)
uv run darkangel-gui     # frontend GUI — attend le backend déjà lancé
uv add <pkg>             # dépendance runtime
uv add --dev <pkg>       # dépendance de dev
uv lock                  # régénérer uv.lock
```

Env : `DARKANGEL_API` surcharge l'URL du backend (défaut
`http://127.0.0.1:8000`).

## Conventions

- Package `src/`, packagé par Hatchling (`[tool.hatch.build.targets.wheel]`).
- Tests : `tests/test_api*.py` (backend), `tests/test_gui_*.py` (frontend).
  Version attendue dans les tests : importez `VERSION` de `darkangel.api`,
  ne codifiez pas la chaîne en dur.
- Le GUI doit rester **constructible offscreen** (pas de `.exec()` dans les
  tests ; `qtbot` fournit l'`QApplication`).

## Note outillage

Pyright/LSP ne résout pas les imports `PySide6.*` ni le champ `transport` de
`ApiClient` (le venv `uv` n'est pas branché sur l'analyseur). Ces diagnostics
sont des **faux positifs** : la suite `uv run pytest` (27 tests) est au vert.
Ne pas "corriger" ces alertes.
