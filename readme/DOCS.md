# Documentation

## Building the documentation site

From the project root:

```bash
pip install mkdocs mkdocs-material
python scripts/build_docs.py
mkdocs build
mkdocs serve
```

Then open `http://127.0.0.1:8000`. The `build_docs.py` script regenerates the API reference and extension API pages from the source code.
