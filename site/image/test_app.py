import re
import unittest

from app import HERE, agent_page, catalog, catalog_page, health, home
from fastapi import HTTPException


AGENTS = HERE.parents[1] / "agents"


class CatalogTests(unittest.TestCase):
    def test_all_configured_agents_have_catalog_pages(self):
        discovered = {path.parent.name for path in AGENTS.glob("*/azure.yaml")}
        self.assertTrue(discovered)
        self.assertEqual(set(catalog), discovered)

        for slug, entry in catalog.items():
            with self.subTest(agent=slug):
                self.assertRegex(slug, r"^[a-z0-9][a-z0-9-]*$")
                self.assertTrue(entry["flow"])
                self.assertTrue(entry["components"])
                self.assertIn(f'href="/agent-catalog/{slug}"', catalog_page())
                self.assertRegex(agent_page(slug), re.compile("<h1>.+</h1>"))
                for component in entry["components"]:
                    source = (AGENTS / slug / component["source"]).resolve()
                    self.assertTrue(source.is_relative_to((AGENTS / slug).resolve()))
                    self.assertTrue(source.exists(), component["source"])

    def test_navigation_and_health(self):
        self.assertEqual(home().headers["location"], "/agent-catalog")
        self.assertEqual(health(), {"status": "ok"})
        self.assertIn('href="/agent-catalog" aria-current="page"', catalog_page())
        for slug in catalog:
            self.assertIn(f'href="/agent-catalog/{slug}" aria-current="page"', agent_page(slug))

        with self.assertRaises(HTTPException) as error:
            agent_page("missing-agent")
        self.assertEqual(error.exception.status_code, 404)
