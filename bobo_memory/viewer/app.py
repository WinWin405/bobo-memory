"""
Optional minimal web viewer for bobo-memory.

Install extras: pip install bobo-memory[viewer]
Run: bobo-memory serve [--port 8765]
"""

from __future__ import annotations


def create_app(project_root: str = "."):
    """Create and return the FastAPI application."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        raise ImportError("Install viewer extras: pip install bobo-memory[viewer]")

    from pathlib import Path
    from bobo_memory import MemoryClient

    app = FastAPI(title="bobo-memory viewer", docs_url=None, redoc_url=None)
    client = MemoryClient(project_root=project_root)

    @app.get("/", response_class=HTMLResponse)
    def index():
        st = client.status()
        rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in st.items()
            if not isinstance(v, dict)
        )
        return f"""
        <html><body>
        <h1>bobo-memory viewer</h1>
        <table border=1>{rows}</table>
        <p><a href="/audit">Audit log</a> | <a href="/status">Status JSON</a> |
        <a href="/layers">Layers</a> | <a href="/storage">Storage</a></p>
        </body></html>
        """

    @app.get("/status")
    def status():
        return client.status()

    @app.get("/audit")
    def audit(limit: int = 50):
        return client.audit_log(limit=limit)

    @app.get("/lint")
    def lint():
        report = client.lint()
        return report.model_dump() if hasattr(report, "model_dump") else report

    @app.get("/proposals")
    def proposals(layer: str | None = None):
        from bobo_memory.tools.proposal import list_proposals
        root = Path(project_root).resolve()
        return list_proposals(root, layer=layer)

    # ---------------- read-only memory API ---------------- #

    @app.get("/layers")
    def layers():
        return {
            "enabled_layers": client.config.enabled_layers,
            "layers": client.status()["layers"],
        }

    @app.get("/memories/{layer}")
    def memories(layer: str):
        result = client.dispatch_tool_call("memory_list", {"layer": layer}, actor="human")
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    @app.get("/memory")
    def memory(file: str):
        result = client.dispatch_tool_call("memory_read", {"file": file}, actor="human")
        if not result.get("ok"):
            status = 404 if "not found" in result.get("error", "").lower() else 400
            return JSONResponse(result, status_code=status)
        return result

    @app.get("/recall")
    def recall(query: str, k: int = 5):
        pack = client.recall(query, k=k)
        return pack.model_dump()

    @app.get("/storage")
    def storage():
        return client.storage_stats()

    return app


def serve(project_root: str = ".", port: int = 8765):
    """Start the viewer server."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError("Install viewer extras: pip install bobo-memory[viewer]")
    app = create_app(project_root)
    uvicorn.run(app, host="127.0.0.1", port=port)
