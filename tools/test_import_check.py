#!/usr/bin/env python3
"""Tests for tools/import_check.py. Run: python3 -I tools/test_import_check.py"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_check  # noqa: E402

HEAD = """@prefix ogit: <http://www.purl.org/ogit/> .
@prefix ogit.T: <http://www.purl.org/ogit/T/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
"""

BASE = {
    "ogit.ttl": HEAD + """
ogit:Entity a rdfs:Class .
ogit:Verb a rdfs:Class .
""",
    "T/entities/A.ttl": HEAD + """
ogit.T:A a rdfs:Class ; rdfs:subClassOf ogit:Entity ;
    rdfs:label "A" ; ogit:description \"\"\"says \\"x\\"\"\"\" ;
    ogit:allowed ( [ ogit.T:links ogit.T:B ] ) .
""",
    "T/entities/B.ttl": HEAD + """
ogit.T:B a rdfs:Class ; rdfs:subClassOf ogit.T:A .
""",
    "T/verbs/links.ttl": HEAD + """
ogit.T:links a owl:ObjectProperty ; rdfs:subPropertyOf ogit:Verb .
""",
}


def run(overrides):
    files = dict(BASE)
    files.update(overrides)
    with tempfile.TemporaryDirectory() as root:
        paths = []
        for rel, text in files.items():
            if text is None:
                continue
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            paths.append(path)
        model, findings = import_check.run(root, sorted(paths))
    return model, sorted({f[0] for f in findings})


class ImportCheck(unittest.TestCase):
    def test_clean_repository_has_no_findings(self):
        model, codes = run({})
        self.assertEqual(codes, [])
        self.assertEqual(len(model["edges"]), 1)
        self.assertEqual(len(model["entities"]), 2)

    def test_c01_undeclared_prefix(self):
        _, codes = run({"T/entities/C.ttl": HEAD + "ogit.T:C a rdfs:Class ; rdfs:subClassOf ogit:Entity ; ogit:allowed ( [ ogit.T:links ogit.X:Y ] ) .\n"})
        self.assertIn("C01", codes)

    def test_c02_class_shaped_verb(self):
        _, codes = run({"T/verbs/links.ttl": HEAD + "ogit.T:links a rdfs:Class ; rdfs:subClassOf ogit:Verb .\n"})
        self.assertEqual(codes, ["C02"])

    def test_c03_file_name_case(self):
        _, codes = run({"T/verbs/links.ttl": None,
                        "T/verbs/Links.ttl": BASE["T/verbs/links.ttl"]})
        self.assertEqual(codes, ["C03"])

    def test_c04_undeclared_verb(self):
        _, codes = run({"T/verbs/links.ttl": None})
        self.assertEqual(codes, ["C04"])

    def test_c05_target_is_not_an_entity(self):
        _, codes = run({"T/entities/B.ttl": None})
        self.assertEqual(codes, ["C05"])

    def test_c06_malformed_cell(self):
        _, codes = run({"T/entities/A.ttl": HEAD + "ogit.T:A a rdfs:Class ; rdfs:subClassOf ogit:Entity ; ogit:allowed ( ogit.T:links ) .\n"})
        self.assertIn("C06", codes)

    def test_c07_duplicate_declaration(self):
        _, codes = run({"T/entities/B2.ttl": HEAD + "ogit.T:B a rdfs:Class ; rdfs:subClassOf ogit.T:A .\n"})
        self.assertIn("C07", codes)

    def test_c08_class_off_the_tree(self):
        _, codes = run({"T/entities/E.ttl": HEAD + "ogit.T:E a rdfs:Class ; rdfs:subClassOf ogit:Enumeration .\n"})
        self.assertEqual(codes, ["C08"])

    def test_from_to_edges_are_collected_and_checked(self):
        verb = HEAD + "ogit.T:has a rdfs:Class ; rdfs:subClassOf ogit:Verb ; ogit:from-to ( [ ogit:from ogit.T:A ; ogit:to ogit.T:Z ] ) .\n"
        model, codes = run({"T/verbs/has.ttl": verb})
        self.assertEqual(codes, ["C02", "C05"])
        self.assertEqual(sum(1 for e in model["edges"] if e[5] == "from-to"), 1)

    def test_parse_error_is_reported_not_raised(self):
        model, codes = run({"T/entities/B.ttl": HEAD + "ogit.T:B a rdfs:Class ; rdfs:label \"open\n"})
        self.assertIsNone(model)
        self.assertEqual(codes, ["PARSE"])


if __name__ == "__main__":
    unittest.main()
