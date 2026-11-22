"""Serve the architecture catalog from the same revision as its container image."""

from html import escape
import json
from pathlib import Path
from string import Template

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "agents"
SOURCE = "https://github.com/ai-platform-portfolio/agent-catalog/tree/main/agents"
LAYOUT = Template((HERE / "index.html").read_text(encoding="utf-8"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=HERE / "static"), name="assets")


def load_agents(directory):
    return {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*/architecture.json"))
    }


catalog = load_agents(AGENTS)


def agent_url(slug):
    return f"/agent-catalog/{slug}"


def render_page(title, heading, content, selected=None):
    tabs = []
    for slug, agent in catalog.items():
        current = ' aria-current="page"' if slug == selected else ""
        tabs.append(
            f'<a class="nav-agent" href="{escape(agent_url(slug))}"{current}>'
            f'{escape(agent["name"])}</a>'
        )
    current = ' aria-current="page"' if selected is None else ""
    return LAYOUT.substitute(
        title=escape(title),
        heading=escape(heading),
        catalog_current=current,
        nav_agents="".join(tabs),
        content=content,
    )


@app.get("/healthz")
def health():
    if not catalog:
        raise HTTPException(status_code=503, detail="Catalog content is missing")
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/agent-catalog", status_code=302)


@app.get("/agent-catalog", response_class=HTMLResponse)
def catalog_page():
    cards = "".join(
        f'<a class="agent-card" href="{escape(agent_url(slug))}">'
        f'<h2>{escape(agent["name"])}</h2><p>{escape(agent["summary"])}</p></a>'
        for slug, agent in catalog.items()
    )
    content = (
        '<p class="intro">Architecture and configuration of the agents in this repository.</p>'
        f'<div class="agent-list">{cards}</div>'
    )
    return render_page("Agent Catalog", "Agent Catalog", content)


@app.get("/agent-catalog/{slug}", response_class=HTMLResponse)
def agent_page(slug: str):
    if slug not in catalog:
        raise HTTPException(status_code=404)
    agent = catalog[slug]
    flow = "".join(
        f'<li><strong>{escape(step["title"])}</strong>'
        f'<p>{escape(step["detail"])}</p></li>'
        for step in agent["flow"]
    )
    components = "".join(
        f'<li><h3>{escape(component["name"])}</h3>'
        f'<p>{escape(component["detail"])}</p>'
        f'<a href="{escape(SOURCE + "/" + slug + "/" + component["source"])}">'
        f'{escape(component["source"])}</a></li>'
        for component in agent["components"]
    )
    content = (
        f'<p class="intro">{escape(agent["summary"])}</p>'
        f'<section class="architecture"><h2>Request flow</h2><ol class="flow">{flow}</ol></section>'
        f'<section class="architecture"><h2>Components</h2><ul class="components">{components}</ul></section>'
    )
    return render_page(agent["name"], agent["name"], content, selected=slug)
