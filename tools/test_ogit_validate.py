#!/usr/bin/env python3
"""Tests for ogit_validate.py.

Each case is a small set of TTL files. Its expected exit status, stdout and
stderr were recorded from bin/ogit-validator.jar and are kept in
testdata/ogit_validate_expected.json; the port must print the same. Paths,
the time of day and the jar's object hash are normalised.

    python3 -I tools/test_ogit_validate.py
    python3 -I tools/test_ogit_validate.py --record   # rewrite the expectations from the jar (needs java)

When java is installed, the cases also run against the jar directly.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ogit_validate as ov  # noqa: E402

REPO = os.path.dirname(HERE)
JAR = os.path.join(REPO, "bin", "ogit-validator.jar")
EXPECTED = os.path.join(HERE, "testdata", "ogit_validate_expected.json")

# rdf4j's wording for a generic Turtle syntax error is not reproduced (see
# the module docstring); only the quoted message differs.
SYNTAX_TEXT_DIFFERS = {"ogit_ttl_skipped"}

P = """@prefix ogit: <http://www.purl.org/ogit/> .
@prefix ogit.X: <http://www.purl.org/ogit/X/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
"""

def ent(name, extra="", scope='"NTO"', parent="ogit:Node", mand="( )", opt="( )", allowed="( )",
        valid='"start=2020-01-01;"', desc='"d"', creator='"c"', ns="ogit.X"):
    body = [f"{ns}:{name} a rdfs:Class", "rdfs:subClassOf ogit:Entity", f'rdfs:label "{name}"']
    if desc is not None: body.append(f"dcterms:description {desc}")
    if valid is not None: body.append(f"dcterms:valid {valid}")
    if creator is not None: body.append(f"dcterms:creator {creator}")
    if scope is not None: body.append(f"ogit:scope {scope}")
    if parent is not None: body.append(f"ogit:parent {parent}")
    if mand is not None: body.append(f"ogit:mandatory-attributes {mand}")
    if opt is not None: body.append(f"ogit:optional-attributes {opt}")
    body.append("ogit:indexed-attributes ( )")
    if allowed is not None: body.append(f"ogit:allowed {allowed}")
    if extra: body.append(extra)
    return P + "\n" + " ;\n  ".join(body) + " .\n"

def prop(kind, name, extra="", ns="ogit.X", valid='"start=2020-01-01;"'):
    body = [f"{ns}:{name} a rdf:Property", f"rdfs:subPropertyOf ogit:{kind}", f'rdfs:label "{name}"',
            'dcterms:description "d"', 'dcterms:creator "c"']
    if valid is not None: body.append(f"dcterms:valid {valid}")
    if extra: body.append(extra)
    return P + "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n\n" + " ;\n  ".join(body) + " .\n"

NODE = ent("Node", ns="ogit", scope='"SGO"', parent="ogit:Node")
BASE = {"OGIT/SGO/sgo/entities/Node.ttl": NODE}

CASES = {
 "ok": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A", mand="( ogit.X:c )", allowed="( [ ogit.X:v ogit.X:A ] )"),
        "OGIT/NTO/X/attributes/c.ttl": prop("Attribute", "c"),
        "OGIT/NTO/X/verbs/v.ttl": prop("Verb", "v", 'ogit:cardinality "one2many"')},
 "node_outside_ogit_dir": {"repo/SGO/sgo/entities/Node.ttl": NODE, "repo/NTO/X/entities/A.ttl": ent("A")},
 "prefix_mismatch": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A").replace("<http://www.purl.org/ogit/X/>", "<http://www.purl.org/ogit/Y/>")},
 "file_name_mismatch": {**BASE, "OGIT/NTO/X/entities/B.ttl": ent("A")},
 "directory_mismatch": {**BASE, "OGIT/NTO/Y/entities/A.ttl": ent("A")},
 "undeclared_prefix": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A", extra="ogit.Q:foo ogit:Node")},
 "single_file": {"OGIT/NTO/Y/entities/B.ttl": ent("A", desc=None)},
 "stage2_each_query": {**BASE,
    "OGIT/NTO/X/entities/A.ttl": ent("A", creator=None, extra='dcterms:created "2020-1-1"'),
    "OGIT/NTO/X/attributes/c.ttl": prop("Attribute", "c", valid=None),
    "OGIT/NTO/X/verbs/v.ttl": prop("Verb", "v", valid=None)},
 "stage2_validation_params": {**BASE,
    "OGIT/NTO/X/attributes/a6.ttl": prop("Attribute", "a6", 'ogit:validation-type "regex"'),
    "OGIT/NTO/X/attributes/a7.ttl": prop("Attribute", "a7", 'ogit:validation-type "regex" ; ogit:validation-parameter ""')},
 "stage2_validation_params_b": {**BASE,
    "OGIT/NTO/X/attributes/a8.ttl": prop("Attribute", "a8", 'ogit:validation-parameter "x" ; ogit:validation-type ""')},
 "stage2_validation_params_c": {**BASE,
    "OGIT/NTO/X/attributes/a9.ttl": prop("Attribute", "a9", 'ogit:validation-parameter "x"')},
 "stage2_history": {**BASE,
    "OGIT/NTO/X/entities/A.ttl": ent("A", extra='ogit:history ( [ dcterms:identifier "1" ; dcterms:date "2020-01-01" ] [ dcterms:identifier "1" ; dcterms:description "d" ; dcterms:creator "c" ; dcterms:date "2020-13" ] )')},
 "stage3_errors": {**BASE,
    "OGIT/NTO/X/entities/A.ttl": ent("A", scope='"XYZ"', parent="ogit.X:Missing", mand="( ogit.X:nope )",
                                     opt="( ogit.X:c ogit.X:gone )", allowed="( [ ogit.X:v ogit.X:Nowhere ] [ ogit.X:noverb ogit.X:A ] )",
                                     desc='"  "', valid='"start=;"'),
    "OGIT/NTO/X/entities/B.ttl": ent("B", parent='"http://www.purl.org/ogit/X/A"', scope='"sgo"'),
    "OGIT/NTO/X/attributes/c.ttl": prop("Attribute", "c"),
    "OGIT/NTO/X/verbs/v.ttl": prop("Verb", "v", 'ogit:cardinality "lots"')},
 "stage3_order": {**BASE,
    "OGIT/SGO/sgo/entities/Abc.ttl": ent("Abc", ns="ogit", scope='"BAD"'),
    "OGIT/NTO/Q/entities/Z.ttl": ent("Z", ns="ogit.Q", scope='"BAD"').replace("ogit.X:", "ogit.Q:").replace("<http://www.purl.org/ogit/X/>", "<http://www.purl.org/ogit/Q/>"),
    "OGIT/NTO/X/entities/M.ttl": ent("M", scope='"BAD"')},
 "stage3_list_head_null": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A", mand='"none"')},
 "ogit_ttl_skipped": {**BASE, "OGIT/ogit.ttl": P + "\nthis is not turtle .\n", "OGIT/NTO/X/entities/A.ttl": ent("A")},
 "stage2_syntax_error_single": {"OGIT/NTO/X/entities/A.ttl": ent("A").replace("ogit:Node", "ogit.Q:Node")},
 "stage3_concat_glue": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A").rstrip("\n") + " # no newline", "OGIT/NTO/X/entities/Z.ttl": "ogit.X:Z rdfs:label \"z\" .\n" + ent("B")},
 "no_ogit_prefix_ok_glue": {**BASE, "OGIT/NTO/X/entities/A.ttl": ent("A").rstrip("\n") + " # trailing comment, no newline"},
}


def norm(text, root):
    out = []
    for line in text.splitlines():
        if "JAVA_TOOL_OPTIONS" in line or line.startswith("\tat ") or line.startswith("\t... "):
            continue
        line = re.sub(r"^\d\d:\d\d:\d\d\.\d{3} ", "TIME ", line)
        line = re.sub(r"FileDataSource@[0-9a-f]+", "FileDataSource@H", line)
        out.append(line.replace(os.path.realpath(root), "<root>"))
    return out


def run_case(files, command):
    d = tempfile.mkdtemp(prefix="ogit-validate-")
    try:
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        root = os.path.join(d, next(iter(files)).split("/")[0])
        args = sorted("./" + os.path.relpath(os.path.join(dp, f), root)
                      for dp, _, fs in os.walk(root) for f in fs)
        r = subprocess.run(command + args, cwd=root, capture_output=True, text=True)
        return [r.returncode, norm(r.stdout, root), norm(r.stderr, root)]
    finally:
        shutil.rmtree(d)


PORT = [sys.executable, "-I", "-B", os.path.join(HERE, "ogit_validate.py")]


def quoted_message_removed(result):
    return [result[0], [re.sub(r'Syntax error: "[^"]*"', 'Syntax error: "…"', x) for x in result[1]],
            [re.sub(r"\[Rio fatal\] .*", "[Rio fatal] …", x) for x in result[2]]]


class Cases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(EXPECTED, encoding="utf-8") as f:
            cls.expected = json.load(f)

    def test_every_case_has_an_expectation(self):
        self.assertEqual(sorted(CASES), sorted(self.expected))

    def test_port_matches_recorded_jar_output(self):
        for name, files in CASES.items():
            with self.subTest(name):
                got, want = run_case(files, PORT), self.expected[name]
                if name in SYNTAX_TEXT_DIFFERS:
                    got, want = quoted_message_removed(got), quoted_message_removed(want)
                self.assertEqual(got, want)

    @unittest.skipUnless(shutil.which("java") and os.path.exists(JAR), "needs java and the jar")
    def test_recorded_output_is_still_the_jars(self):
        for name in ("ok", "directory_mismatch", "stage3_errors"):
            with self.subTest(name):
                self.assertEqual(run_case(CASES[name], ["java", "-jar", JAR]), self.expected[name])


class Order(unittest.TestCase):
    def test_store_order_is_lexical(self):
        nodes = [("lit", "a", None, ov.XSD + "string"), ("iri", "http://b"), ("bnode", "x"),
                 ("iri", "http://a"), ("lit", "a", "en", None), ("lit", "1", None, ov.XSD + "int")]
        got = sorted(nodes, key=ov.node_key)
        self.assertEqual(got, [("bnode", "x"), ("iri", "http://a"), ("iri", "http://b"),
                               ("lit", "1", None, ov.XSD + "int"), ("lit", "a", "en", None),
                               ("lit", "a", None, ov.XSD + "string")])

    def test_java_string_order_is_utf16(self):
        # U+FFFF sorts after U+10000 in UTF-16, before it by code point.
        self.assertLess(ov.jstr("\U00010000"), ov.jstr("\uffff"))


class AllFlag(unittest.TestCase):
    def test_all_lists_every_failing_subject(self):
        files = dict(BASE)
        for n in ("A", "B", "C"):
            files[f"OGIT/NTO/X/entities/{n}.ttl"] = ent(n, desc=None)
        one = run_case(files, PORT)
        every = run_case(files, PORT + ["--all"])
        self.assertEqual(len([x for x in one[1] if "validation0" in x]), 1)
        self.assertEqual(len([x for x in every[1] if "validation0" in x]), 3)


def record():
    out = {name: run_case(files, ["java", "-jar", JAR]) for name, files in CASES.items()}
    with open(EXPECTED, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"recorded {len(out)} cases in {EXPECTED}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--record"]:
        record()
    else:
        unittest.main()
