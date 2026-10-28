#!/usr/bin/env python3
"""Python port of bin/ogit-validator.jar.

`validate.sh` runs the OGIT validator, a Scala/Java jar from 2019. This
script does the same checks in Python, so the ontology can be validated
where no Java is installed. It takes the same arguments and prints the
same messages, on the same streams, with the same exit status:

    python3 -I tools/ogit_validate.py $(find . -type f ! -name 'graphit-ontology.*' -name '*.ttl')

or, as `validate.sh` does, through xargs. With no arguments it finds the
files the way `validate.sh` does, from the current directory.

The behaviour was read from the jar's bytecode (`co.arago.meerkat.validator`
and `de.arago.ogit.tools`) and checked against the jar on the repository and
on fixtures (`tools/test_ogit_validate.py`). The jar runs three stages; a
stage runs only if the one before it passed.

1. Per file, only when more than one file is given:
   - every `@prefix ogit...:` line must declare
     `http://www.purl.org/` + the prefix with `.` replaced by `/` + `/`;
     the first one that doesn't stops the run;
   - files whose path contains `ogit.ttl` are not parsed; every other file
     is parsed on its own, and a Turtle error stops the run with a Java
     stack trace;
   - the file name must be the local name of the file's first subject plus
     `.ttl` (reported, not fatal);
   - the file's canonical path must match `/<namespace dir>/(.*)/<name>`,
     case-insensitive; the first one that doesn't stops the run. The
     namespace dir is the subject IRI's path between `www.purl.org/` and the
     local name, with `ogit/` removed.
2. Ten SPARQL queries (`validation0.rq` ... `validation9.rq` in the jar) run
   over all files together. Each failing query prints one line.
3. The entity registry: every referenced attribute, parent, allowed target
   and allowed verb must exist, scopes and cardinalities must be known, and
   entities, verbs and attributes need a label, a description, a creator and
   a `dcterms:valid` start date.

Order. rdf4j's in-memory store commits each add through a sorted TreeModel,
so statements come back sorted (LexicalValueComparator: blank nodes, IRIs,
literals; IRIs by string), not in file order. This port sorts the same way.
That decides the first subject of a file in stage 1, and the order of the
stage 3 messages and the value read when a property has several.

The directory check uses the canonical path. Root-namespace files such as
SGO/sgo/entities/Node.ttl pass only because the checkout itself is named
OGIT (or ogit): the pattern /ogit/(.*)/node.ttl then matches. In a checkout
under another name the same files fail.

Known differences from the jar:
  - Stage 2 names one subject per failing query: the first group the query
    engine produces. That order depends on rdf4j's grouping and on the order
    of the arguments, and no model of it reproduced the jar. This port
    reports the first failing subject in store order; the set of failing
    queries is the same. `--all` lists every subject.
  - The prefix error names the jar's data-source object, which Java prints
    as `co.arago.meerkat.validator.FileDataSource@<hash>`. The hash is not
    reproducible; this port prints a hash of the path.
  - Turtle syntax errors are reported with this parser's message and line,
    not rdf4j's (except for an undeclared prefix, whose message matches).
  - Only the Turtle this repository uses is parsed (see import_check.py),
    plus `^^` datatypes and string escapes.

Exit status: 0 valid, 1 invalid.
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_check as ic  # noqa: E402

OGIT = "http://www.purl.org/ogit/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
DCT = "http://purl.org/dc/terms/"
XSD = "http://www.w3.org/2001/XMLSchema#"

RDF_FIRST, RDF_REST, RDF_NIL = RDF + "first", RDF + "rest", RDF + "nil"
SUBCLASS, SUBPROP, LABEL = RDFS + "subClassOf", RDFS + "subPropertyOf", RDFS + "label"

# Prefixes rdf4j 2.1.3's Turtle parser resolves without a declaration
# (BasicParserSettings / Namespaces.DEFAULT_RDF4J).
DEFAULT_PREFIXES = {
    "cat": "http://www.w3.org/ns/dcat#", "qb": "http://purl.org/linked-data/cube#",
    "grddl": "http://www.w3.org/2003/g/data-view#", "ma": "http://www.w3.org/ns/ma-ont#",
    "owl": "http://www.w3.org/2002/07/owl#", "rdf": RDF, "rdfa": "http://www.w3.org/ns/rdfa#",
    "rdfs": RDFS, "rif": "http://www.w3.org/2007/rif#", "rr": "http://www.w3.org/ns/r2rml#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "skosxl": "http://www.w3.org/2008/05/skos-xl#", "wdr": "http://www.w3.org/2007/05/powder#",
    "void": "http://rdfs.org/ns/void#", "wdrs": "http://www.w3.org/2007/05/powder-s#",
    "xhv": "http://www.w3.org/1999/xhtml/vocab#", "xml": "http://www.w3.org/XML/1998/namespace",
    "xsd": XSD, "prov": "http://www.w3.org/ns/prov#", "sd": "http://www.w3.org/ns/sparql-service-description#",
    "org": "http://www.w3.org/ns/org#", "gldp": "http://www.w3.org/ns/people#",
    "cnt": "http://www.w3.org/2008/content#", "dcat": "http://www.w3.org/ns/dcat#",
    "earl": "http://www.w3.org/ns/earl#", "ht": "http://www.w3.org/2006/http#",
    "ptr": "http://www.w3.org/2009/pointers#", "cc": "http://creativecommons.org/ns#",
    "ctag": "http://commontag.org/ns#", "dc": DCT, "dc11": "http://purl.org/dc/elements/1.1/",
    "dcterms": DCT, "foaf": "http://xmlns.com/foaf/0.1/", "gr": "http://purl.org/goodrelations/v1#",
    "ical": "http://www.w3.org/2002/12/cal/icaltzd#", "og": "http://ogp.me/ns#",
    "rev": "http://purl.org/stuff/rev#", "sioc": "http://rdfs.org/sioc/ns#",
    "v": "http://rdf.data-vocabulary.org/#", "vcard": "http://www.w3.org/2006/vcard/ns#",
    "schema": "http://schema.org/",
}

RESET, RED, YELLOW = "\x1b[0m", "\x1b[31m", "\x1b[33m"


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

class Out:
    def __init__(self, color):
        self.color = color

    def failed(self, message):
        """The jar's ColoredErrorFormatter line, printed to stdout."""
        if self.color:
            print(f"{RED}Validation failed{RESET}: {YELLOW}{message}{RESET}")
        else:
            print(f"Validation failed: {message}")


class JavaCrash(Exception):
    """An exception the jar does not catch; Java prints it and exits 1."""


class TurtleError(Exception):
    """A Turtle syntax error, as rdf4j's RDFParseException reports it."""

    def __init__(self, message, line):
        super().__init__(f"{message} [line {line}]")
        self.message, self.line = message, line

    def log(self):
        """rdf4j's ParseErrorLogger line, which logback writes to stderr."""
        stamp = time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000)
        print(f"{stamp} [main] ERROR o.e.r.rio.helpers.ParseErrorLogger - [Rio fatal] "
              f"{self.message} ({self.line}, -1)", file=sys.stderr)

    def crash(self):
        self.log()
        return JavaCrash(f"org.eclipse.rdf4j.rio.RDFParseException: {self}")


# --------------------------------------------------------------------------
# RDF model
# --------------------------------------------------------------------------

def unescape(s):
    def repl(m):
        e = m.group(1)
        if e[0] in "uU":
            return chr(int(e[1:], 16))
        return {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f"}.get(e, e)
    return re.sub(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)", repl, s, flags=re.S)


NUMERIC = {XSD + t for t in (
    "decimal integer long int short byte double float nonPositiveInteger negativeInteger "
    "nonNegativeInteger positiveInteger unsignedLong unsignedInt unsignedShort unsignedByte").split()}
CALENDAR = {XSD + t for t in
            "dateTime dateTimeStamp time date gYearMonth gYear gMonthDay gDay gMonth".split()}


def jstr(s):
    """Sort key for java.lang.String.compareTo (UTF-16 code units)."""
    return s.encode("utf-16-be", "surrogatepass")


def node_key(n):
    """rdf4j LexicalValueComparator: blank nodes, then IRIs, then literals."""
    if n[0] == "bnode":
        return (0, jstr(n[1]))
    if n[0] == "iri":
        return (1, jstr(n[1]))
    dt = n[3] if n[3] is not None else RDF + "langString"
    group = 0 if dt in NUMERIC else 1 if dt in CALENDAR else 2
    lang = (0, b"") if n[2] is None else (1, jstr(n[2]))
    return (2, group, jstr(dt), lang, jstr(n[1]))


class Model:
    """A repository's statements as rdf4j's MemoryStore returns them.

    The store commits each add through a TreeModel, so statements come back
    sorted by subject, predicate and object (LexicalValueComparator), not in
    file order; duplicates collapse.

    A node is ("iri", value), ("bnode", id) or ("lit", label, lang, datatype).
    """

    def __init__(self):
        self.triples = []
        self._seen = set()
        self._bnodes = 0

    def add(self, s, p, o):
        t = (s, p, o)
        if t not in self._seen:
            self._seen.add(t)
            self.triples.append(t)

    def bnode(self):
        self._bnodes += 1
        return ("bnode", f"node{self._bnodes}")

    def node(self, term):
        """Turn a parsed term into a node, adding the triples it implies.

        rdf4j reports the triples inside `[ ... ]` and `( ... )` before the
        triple that uses the blank node, so they are added first.
        """
        if term.kind == "iri":
            return ("iri", term.value)
        if term.kind == "lit":
            dt = term.datatype
            if term.lang is not None:
                return ("lit", unescape(term.value), term.lang.lower(), None)
            return ("lit", unescape(term.value), None, dt or XSD + "string")
        if term.kind == "bnode":
            b = self.bnode()
            for p, o in term.value:
                on = self.node(o)
                self.add(b, ("iri", p.value), on)
            return b
        if term.kind == "list":
            if not term.value:
                return ("iri", RDF_NIL)
            head = cell = self.bnode()
            for k, item in enumerate(term.value):
                self.add(cell, ("iri", RDF_FIRST), self.node(item))
                nxt = self.bnode() if k + 1 < len(term.value) else ("iri", RDF_NIL)
                self.add(cell, ("iri", RDF_REST), nxt)
                cell = nxt
            return head
        raise ValueError(term.kind)

    def load(self, fp):
        for subject, pairs in fp.statements:
            s = self.node(subject)
            for p, o in pairs:
                self.add(s, ("iri", p.value), self.node(o))

    def index(self):
        self.triples.sort(key=lambda t: (node_key(t[0]), node_key(t[1]), node_key(t[2])))
        self.by_sp, self.by_s, self.by_p = {}, {}, {}
        for s, p, o in self.triples:
            self.by_sp.setdefault((s, p), []).append(o)
            self.by_s.setdefault(s, []).append((p, o))
            self.by_p.setdefault(p, []).append((s, o))
        return self

    def objs(self, s, p):
        return self.by_sp.get((s, ("iri", p)), [])

    def subjects(self, p, o):
        return [s for s, oo in self.by_p.get(("iri", p), []) if oo == ("iri", o)]


def string_value(n):
    return n[1]


def to_string(n):
    """rdf4j Value.toString()."""
    if n[0] == "iri":
        return n[1]
    if n[0] == "bnode":
        return "_:" + n[1]
    if n[2] is not None:
        return f'"{n[1]}"@{n[2]}'
    return f'"{n[1]}"^^<{n[3]}>'


def parse_text(path, text):
    """Parse Turtle as rdf4j does; return a FileParse.

    Raises TurtleError; the message is rdf4j's for an undeclared prefix and
    this parser's otherwise.
    """
    try:
        fp = parse_with_datatypes(path, text)
    except ic.ParseError as e:
        m = re.match(r"line (\d+): (.*)", str(e), re.S)
        if m:
            raise TurtleError(m.group(2), int(m.group(1))) from e
        raise TurtleError(str(e), text.count("\n") + 1) from e
    if fp.undeclared:
        line, word = fp.undeclared[0]
        prefix = word.split(":", 1)[0]
        raise TurtleError(f"Namespace prefix '{prefix}' used but not defined", line)
    return fp


DT_RE = re.compile(r"\^\^(<[^>]*>|[^\s()\[\];,\"'<#]+?)(?=[\s()\[\];,.]|$)")
DT_MARK = "zzdatatype-"


def parse_with_datatypes(path, text):
    """import_check's parser does not read `^^datatype`. Each datatype is
    replaced by a language-tag marker before parsing and restored after."""
    found = []

    def cut(m):
        found.append(m.group(1))
        return f"@{DT_MARK}{len(found) - 1}"

    marked = DT_RE.sub(cut, text) if "^^" in text else text
    fp = ic.parse(path, marked, default_prefixes=DEFAULT_PREFIXES)
    if found:
        restore_datatypes(fp, found)
    return fp


def restore_datatypes(fp, found):
    def walk(t):
        if t.kind == "lit" and t.lang and t.lang.startswith(DT_MARK):
            dt = found[int(t.lang[len(DT_MARK):])]
            if dt.startswith("<"):
                t.datatype = dt[1:-1]
            else:
                pre, local = dt.split(":", 1)
                t.datatype = fp.prefixes.get(pre, DEFAULT_PREFIXES.get(pre, "")) + local
            t.lang = None
        elif t.kind == "list":
            for x in t.value:
                walk(x)
        elif t.kind == "bnode":
            for _, o in t.value:
                walk(o)

    for subject, pairs in fp.statements:
        walk(subject)
        for _, o in pairs:
            walk(o)


def read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def unfold(path):
    """Validator.unfoldDirectories: a directory contributes the entries whose
    name ends in `.ttl`, in listing order; subdirectories are not entered."""
    if os.path.isdir(path):
        out = []
        for name in os.listdir(path):
            if name.endswith(".ttl"):
                out.extend(unfold(os.path.join(path, name)))
        return out
    return [path]


def java_path_parts(s):
    """Components of java.nio.file.Paths.get(s) on Unix."""
    return [c for c in s.split("/") if c]


def java_path_str(s):
    parts = java_path_parts(s)
    return ("/" if s.startswith("/") else "") + "/".join(parts)


# --------------------------------------------------------------------------
# Stage 1: per file
# --------------------------------------------------------------------------

PREFIX_RE = re.compile(r"^\s*@prefix\s+([^\s]+):\s+<([^>]+)>\s+\.", re.ASCII)
LINE_SPLIT = re.compile("\r\n|[\n\r  \u0085]")


def stage1(out, path, text):
    """Return False to stop the run."""
    canonical = os.path.realpath(path)
    base = os.path.basename(canonical)
    for line in LINE_SPLIT.split(text):
        if "@prefix" not in line:
            continue
        m = PREFIX_RE.search(line)
        if not m or not m.group(1).startswith("ogit"):
            continue
        expected = "http://www.purl.org/" + m.group(1).replace(".", "/") + "/"
        if m.group(2) != expected:
            source = "co.arago.meerkat.validator.FileDataSource@%x" % (hash(canonical) & 0xFFFFFFF)
            msg = (f"Error in validation query {source}: {base} Declared prefix {m.group(1)} "
                   f"does not match expected ogit convention {expected}. declared as {m.group(2)}")
            out.failed(f'Syntax error: "{msg}" at Source:  <no location data available>')
            return False
    if "ogit.ttl" in canonical:
        return True
    try:
        fp = parse_text(path, text)
    except TurtleError as e:
        raise e.crash() from e
    model = Model()
    model.load(fp)
    model.index()
    subject = next((s for s, _, _ in model.triples if s[0] == "iri"), None)
    subject_str = subject[1] if subject else "Optional.empty"
    parts = java_path_parts(subject_str)
    name = parts[-1] if parts else ""
    if name + ".ttl" != base:
        print("file name is not same as namespace name")
        out.failed(f"File Name mismatch: {base} does not match with name: {name}")
    n = len(parts)
    if not (2 < n - 1):
        raise JavaCrash("java.lang.IllegalArgumentException")
    directory = "/".join(parts[2:n - 1]).replace("ogit/", "")
    pattern = "/" + directory.lower() + "/(.*)/" + name.lower()
    if not re.search(pattern, canonical.lower()):
        out.failed(f"Directory mismatch:  {base} should be under directory {directory}, "
                   f"according to {java_path_str(subject_str)}")
        return False
    return True


# --------------------------------------------------------------------------
# Stage 2: the ten queries
# --------------------------------------------------------------------------

def stage2(out, model, show_all):
    E, A, V = OGIT + "Entity", OGIT + "Attribute", OGIT + "Verb"
    ents = model.subjects(SUBCLASS, E)
    atts = model.subjects(SUBPROP, A)
    verbs = model.subjects(SUBPROP, V)
    members = list(dict.fromkeys(ents + atts + verbs))

    def has(x, p):
        return bool(model.objs(x, p))

    def missing(x, props):
        return " ".join(p for p in props if not has(x, p))

    def tnames(x):
        # BIND(SUBSTR(STR(?t), STRLEN(STR(ogit:)) + 1) AS ?tname)
        return [string_value(t)[len(OGIT):] for t in model.objs(x, SUBCLASS) + model.objs(x, SUBPROP)]

    def history_items(x):
        """?x ogit:history/rdf:rest*/rdf:first ?h"""
        items, seen = [], set()
        frontier = list(model.objs(x, OGIT + "history"))
        while frontier:
            cell = frontier.pop(0)
            if cell in seen or cell[0] == "lit":
                continue
            seen.add(cell)
            for f in model.objs(cell, RDF_FIRST):
                if f not in items:
                    items.append(f)
            frontier.extend(model.objs(cell, RDF_REST))
        return items

    failures = []

    def report(desc, n):
        failures.append(f"{desc} (/validation{n}.rq)")

    def required(n, kind, xs, props):
        bad = [(x, missing(x, props)) for x in xs if missing(x, props)]
        for x, miss in (bad if show_all else bad[:1]):
            report(f"{kind} {string_value(x)} is missing property(ies): {miss}", n)

    # validation0 / 4 / 5
    v0 = [LABEL, DCT + "description", DCT + "valid", DCT + "creator", OGIT + "scope",
          OGIT + "parent", OGIT + "mandatory-attributes", OGIT + "optional-attributes", OGIT + "allowed"]
    base = [LABEL, DCT + "description", DCT + "valid", DCT + "creator"]
    results = {}
    required(0, "Entity", ents, v0)
    results[0] = list(failures)
    failures.clear()

    # validation1: history items missing properties
    hprops = [DCT + "identifier", DCT + "description", DCT + "creator", DCT + "date"]
    for x in members:
        for h in history_items(x):
            miss = " ".join(p for p in hprops if not (h[0] != "lit" and model.objs(h, p)))
            if miss:
                for t in tnames(x)[:1] if not show_all else tnames(x):
                    report(f"{t} {string_value(x)} is missing property(ies) in one of its "
                           f"ogit:history items: {miss}", 1)
                break
        if failures and not show_all:
            break
    results[1] = list(failures)
    failures.clear()

    # validation2: duplicate history identifiers
    for x in members:
        counts = {}
        for h in history_items(x):
            if h[0] == "lit":
                continue
            for i in model.objs(h, DCT + "identifier"):
                counts[i] = counts.get(i, 0) + 1
        for i, c in counts.items():
            if c > 1:
                for t in tnames(x)[:1] if not show_all else tnames(x):
                    suffix = f" ({string_value(i)})" if i[0] == "lit" else ""
                    report(f"{t} {string_value(x)} has duplicate dcterms:identifier in its "
                           f"ogit:history items{suffix}", 2)
        if failures and not show_all:
            break
    results[2] = list(failures)
    failures.clear()

    # validation3: date format
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    for x in members:
        ds = model.objs(x, DCT + "created") + model.objs(x, DCT + "modified")
        for h in history_items(x):
            if h[0] != "lit":
                ds += model.objs(h, DCT + "date")
        if any(not date_re.fullmatch(string_value(d)) for d in ds):
            for t in tnames(x)[:1] if not show_all else tnames(x):
                report(f"{t} {string_value(x)} has invalid value for dcterms:created/modified/date: "
                       f"Should be in ISO 8601 format (YYYY-MM-DD)", 3)
        if failures and not show_all:
            break
    results[3] = list(failures)
    failures.clear()

    required(4, "Attribute", atts, base)
    results[4] = list(failures)
    failures.clear()
    required(5, "Verb", verbs, base)
    results[5] = list(failures)
    failures.clear()

    # validation6..9: the subquery picks one subject among ALL subjects
    # (LIMIT 1), then the outer query keeps it only if it is an attribute.
    att_set = set(atts)
    vt, vp = OGIT + "validation-type", OGIT + "validation-parameter"
    all_subjects = list(dict.fromkeys(s for s, _, _ in model.triples))

    def empty_lit(n):
        return n[0] == "lit" and n[1] == "" and n[2] is None and n[3] == XSD + "string"

    def limited(n, cands, outer, desc):
        picks = cands if show_all else cands[:1]
        for x in picks:
            if outer(x):
                report(desc(x), n)

    limited(6, [x for x in all_subjects if has(x, vt) and not has(x, vp)],
            lambda x: x in att_set,
            lambda x: f"Attribute {string_value(x)} is missing property(ies): {vp}")
    results[6] = list(failures)
    failures.clear()
    limited(7, [x for x in all_subjects if any(empty_lit(o) for o in model.objs(x, vp))],
            lambda x: x in att_set and has(x, vt),
            lambda x: f"Attribute {string_value(x)} has empty value for ogit:validation-parameter(ies): ")
    results[7] = list(failures)
    failures.clear()
    limited(8, [x for x in all_subjects if any(empty_lit(o) for o in model.objs(x, vt))],
            lambda x: x in att_set and has(x, vp),
            lambda x: f"Attribute {string_value(x)} has empty value for ogit:validation-type(ies): ")
    results[8] = list(failures)
    failures.clear()
    limited(9, [x for x in all_subjects if has(x, vp) and not has(x, vt)],
            lambda x: x in att_set,
            lambda x: f"Attribute {string_value(x)} is missing property(ies): {vt}")
    results[9] = list(failures)
    failures.clear()

    lines = [line for n in range(10) for line in results[n]]
    for line in lines:
        out.failed(line)
    return not lines


# --------------------------------------------------------------------------
# Stage 3: the entity registry (TTLLoader + OgitValidator)
# --------------------------------------------------------------------------

class RegistryError(Exception):
    pass


def stage3(model):
    """Return (errors, warnings), or raise RegistryError with the loader's message."""
    def first(s, p):
        o = model.objs(s, p)
        return string_value(o[0]) if o else None

    def first_resource(s, p):
        for o in model.objs(s, p):
            if o[0] != "lit":
                return o
        return None

    def as_values(head):
        if head is None:
            raise RegistryError("list head may not be null")
        vals, cell, seen = [], head, set()
        while cell != ("iri", RDF_NIL):
            if cell in seen:
                raise RegistryError("list is cyclic")
            seen.add(cell)
            f = model.objs(cell, RDF_FIRST)
            r = model.objs(cell, RDF_REST)
            if not f or not r:
                raise RegistryError(f"incomplete list at {to_string(cell)}")
            vals.append(f[0])
            cell = r[0]
            if cell[0] == "lit":
                raise RegistryError("list rest is not a resource")
        return vals

    def attributes(s, p):
        vals = as_values(first_resource(s, p))
        return [to_string(v) for v in vals] or None

    def parse_date(value, which):
        if value is None:
            raise RegistryError("null")
        found = ""
        for m in re.finditer(which + r"=(.*?);", value):
            found = m.group(1)
        if found == "":
            return None
        return True if re.match(r"\s*[+-]?\d+-[+-]?\d+-[+-]?\d+", found) else None

    def common(s):
        valid = first(s, DCT + "valid")
        return {
            "id": string_value(s),
            "name": first(s, LABEL),
            "description": first(s, DCT + "description"),
            "valid-from": parse_date(valid, "start"),
            "valid-until": parse_date(valid, "end"),
            "creator": first(s, DCT + "creator"),
        }

    def allowed(s):
        head = first_resource(s, OGIT + "allowed")
        if head is None:
            return None
        out = []
        for v in as_values(head):
            if v[0] == "lit":
                raise RegistryError("class cast")
            pairs = model.by_s.get(v, [])
            if not pairs:
                raise RegistryError(None)
            out.append({"with": to_string(pairs[0][0]), "to": string_value(pairs[0][1])})
        return out

    entities, attrs, verbs = {}, {}, {}
    for s in model.subjects(SUBCLASS, OGIT + "Entity"):
        item = common(s)
        item["scope"] = first(s, OGIT + "scope")
        item["parent"] = first(s, OGIT + "parent")
        optional = attributes(s, OGIT + "optional-attributes")
        mandatory = attributes(s, OGIT + "mandatory-attributes")
        attributes(s, OGIT + "indexed-attributes")
        item["mandatory"], item["optional"] = mandatory, optional
        item["allowed"] = allowed(s)
        entities[to_string(s)] = item
    for s in model.subjects(SUBPROP, OGIT + "Attribute"):
        attrs[to_string(s)] = common(s)
    for s in model.subjects(SUBPROP, OGIT + "Verb"):
        item = common(s)
        item["cardinality"] = first(s, OGIT + "cardinality")
        verbs[to_string(s)] = item

    errors = []

    def blank(v):
        return v is None or v.strip(" \t\n\r\x0b\x0c" + "".join(map(chr, range(0, 33)))) == ""

    def base(kind, items):
        for item in items.values():
            i = item["id"]
            if blank(item["description"]):
                errors.append(f"{kind} id: {i} , description missing!")
            if blank(item["name"]):
                errors.append(f"{kind} id: {i} , label missing!")
            if item["valid-from"] is None:
                errors.append(f"{kind} id: {i} , valid from missing!")
            if blank(item["creator"]):
                errors.append(f"{kind} id: {i} , creator missing!")

    def j(v):
        return "null" if v is None else v

    base("entities", entities)
    for item in entities.values():
        i = item["id"]
        scope = item["scope"]
        if scope is not None and scope.lower() not in ("sgo", "nto"):
            errors.append(f"type id: {i} , unknown scope {scope}")
        parent = item["parent"]
        if parent is not None:
            if parent not in entities:
                errors.append(f"type id: {i} , parent id : {parent} doesn't exist!")
        elif i.lower() != (OGIT + "Node").lower():
            errors.append(f"type id: {i} , parent id missing!")
        for key in ("mandatory", "optional"):
            for name in item[key] or []:
                if name not in attrs:
                    errors.append(f"type id: {i} , attribute id : {name} doesn't exist!")
        for edge in item["allowed"] or []:
            if edge["to"] not in entities:
                errors.append(f"edge id: {i} , head connection id : {j(edge['to'])} doesn't exist!")
            if edge["with"] not in verbs:
                errors.append(f"edge id: {i} , connection id e: {j(edge['with'])} doesn't exist!")
    base("verbs", verbs)
    for item in verbs.values():
        card = item["cardinality"]
        if card is not None and card.lower() not in ("one2one", "one2many", "many2one", "many2many"):
            errors.append(f"edge id: {item['id']} , unknown cardinality {card}")
    base("attributes", attrs)
    return errors, []


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def default_files():
    out = []
    for root, dirs, files in os.walk("."):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(".ttl") and not f.startswith("graphit-ontology."):
                out.append(os.path.join(root, f))
    return out


def validate(paths, color=True, show_all=False):
    out = Out(color)
    files = []
    for p in paths:
        files.extend(unfold(p))
    if not files:
        out.failed("No input files specified")
        return 1
    texts = {}
    try:
        for f in files:
            if not os.path.exists(f):
                out.failed(f"IO Error: {f}")
                return 1
            texts[f] = read(f)
        if len(files) > 1:
            for f in files:
                if not stage1(out, f, texts[f]):
                    return 1
        model = Model()
        failed = False
        for f in files:
            try:
                model.load(parse_text(f, texts[f]))
            except TurtleError as e:
                e.log()
                out.failed(f'Syntax error: "{e}" at Source: {os.path.realpath(f)} '
                           f"Line: {e.line} Column: -1 ")
                failed = True
        if failed:
            return 1
        model.index()
        if not stage2(out, model, show_all):
            return 1
        # Stage 3 reads all files as one concatenated stream.
        registry = Model()
        try:
            registry.load(parse_concatenated(files, texts))
            registry.index()
            errors, warnings = stage3(registry)
        except (RegistryError, TurtleError) as e:
            if isinstance(e, TurtleError):
                e.log()
            msg = str(e)
            print(f"Error while loading entity registry: {msg}", file=sys.stderr)
            # The caller then reads the registry's result map (MV@334).
            raise JavaCrash('java.lang.NullPointerException: Cannot invoke '
                            '"java.util.Map.get(Object)" because "ontMap" is null') from e
    except JavaCrash as e:
        print(f'Exception in thread "main" {e}', file=sys.stderr)
        return 1
    if errors:
        print(f"Count of errors: {len(errors)}", file=sys.stderr)
        print(f"Count of warnings: {len(warnings)}", file=sys.stderr)
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        return 1
    if warnings:
        print(f"Count of warnings: {len(warnings)}", file=sys.stderr)
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
    print("Validation successful.")
    return 0


def parse_concatenated(files, texts):
    # SequenceInputStream adds no separator between files.
    return parse_text("<stream>", "".join(texts[f] for f in files))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="TTL files or directories (default: as validate.sh)")
    ap.add_argument("--no-color", action="store_true", help="omit the ANSI colours the jar prints")
    ap.add_argument("--all", action="store_true",
                    help="stage 2: list every failing subject, not one per query")
    args = ap.parse_args(argv)
    files = args.files or default_files()
    sys.stdout.reconfigure(line_buffering=True)
    return validate(files, color=not args.no_color, show_all=args.all)


if __name__ == "__main__":
    sys.exit(main())
