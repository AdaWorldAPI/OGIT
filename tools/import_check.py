#!/usr/bin/env python3
"""Strict OGIT consistency check for importers.

`validate.sh` runs the OGIT validator, which checks what the OGIT project
requires of its own files. A program that imports OGIT into another schema
needs more: every verb an entity allows must be declared, every allowed
target must be an entity, and every verb must have the one declaration shape
an importer can read. This script checks those import-time rules and can
write the verb catalogue and the allowed (source, verb, target) table.

It is not part of `validate.sh` or CI, and it changes no file. Run it before
an import:

    python3 -I tools/import_check.py
    python3 -I tools/import_check.py --emit-verbs verbs.tsv --emit-allowed allowed.tsv

Exit status: 0 no errors, 1 errors found, 2 a file could not be parsed or
the arguments were wrong. `--warn CODE` reports a check without failing.

Checks:
  C01  a prefixed name uses a prefix the file does not declare
  C02  a verb has neither supported shape: `owl:ObjectProperty` +
       `rdfs:subPropertyOf ogit:Verb`, or `rdfs:Class` + `rdfs:subClassOf
       ogit:Verb` with an `ogit:from-to` list (the verb-as-class form)
  C03  the file name differs from the local name of the term it declares
  C04  an `ogit:allowed` or `ogit:from-to` verb is not declared anywhere
  C05  an `ogit:allowed` or `ogit:from-to` target is not a declared entity
  C06  an `ogit:allowed` cell is not `[ verb target ]` with one pair
  C07  a term is declared in more than one file
  C08  a class is not a subclass, directly or through declared classes,
       of `ogit:Entity`, `ogit:Verb` or another OGIT base class

Only the Turtle used in this repository is parsed: `@prefix`, prefixed
names, `<IRI>`, string literals (including triple-quoted ones and language
tags), lists, blank nodes, `a`, and the `;` `,` `.` separators.
"""

import argparse
import os
import re
import sys
from collections import defaultdict

OGIT = "http://www.purl.org/ogit/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"

RDFS_CLASS = RDFS + "Class"
SUBCLASS = RDFS + "subClassOf"
SUBPROP = RDFS + "subPropertyOf"
OBJECT_PROPERTY = OWL + "ObjectProperty"
ENTITY = OGIT + "Entity"
VERB = OGIT + "Verb"
ALLOWED = OGIT + "allowed"
FROM_TO = OGIT + "from-to"
FROM = OGIT + "from"
TO = OGIT + "to"

ALL_CODES = ["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08"]

SEPARATORS = set("()[];,")


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

def tokenize(text):
    """Yield (kind, value, line) for the Turtle subset described above."""
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "<":
            j = text.find(">", i)
            if j < 0:
                raise ParseError(f"line {line}: unterminated IRI")
            yield ("iri", text[i + 1:j], line)
            i = j + 1
        elif c in "\"'":
            q3 = text[i:i + 3]
            if q3 in ('"""', "'''"):
                j = i + 3
                while j < n and text[j:j + 3] != q3:
                    j += 2 if text[j] == "\\" else 1
                if j >= n:
                    raise ParseError(f"line {line}: unterminated long string")
                value = text[i + 3:j]
                start_line = line
                line += value.count("\n")
                i = j + 3
            else:
                j = i + 1
                while j < n and text[j] != c:
                    if text[j] == "\\":
                        j += 1
                    if j < n and text[j] == "\n":
                        raise ParseError(f"line {line}: newline in string")
                    j += 1
                if j >= n:
                    raise ParseError(f"line {line}: unterminated string")
                value = text[i + 1:j]
                start_line = line
                i = j + 1
            lang = None
            if i < n and text[i] == "@":
                m = re.compile(r"@[A-Za-z]+(-[A-Za-z0-9]+)*").match(text, i)
                if m:
                    lang = m.group(0)[1:]
                    i = m.end()
            yield ("lit", (value, lang), start_line)
        elif c in SEPARATORS:
            yield (c, c, line)
            i += 1
        elif c == ".":
            yield (".", ".", line)
            i += 1
        else:
            m = re.compile(r"[^\s()\[\];,\"'<#]+").match(text, i)
            word = m.group(0)
            i = m.end()
            # A local name cannot end with '.', so a trailing dot closes
            # the statement ("ogit:Node." is "ogit:Node" then ".").
            tail = 0
            while word.endswith(".") and len(word) > 1:
                word = word[:-1]
                tail += 1
            yield ("word", word, line)
            for _ in range(tail):
                yield (".", ".", line)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class Term:
    __slots__ = ("kind", "value", "line", "curie", "lang", "datatype")

    def __init__(self, kind, value, line, curie=None, lang=None):
        self.kind = kind      # iri | lit | list | bnode
        self.value = value    # str | list[Term] | list[(Term, Term)]
        self.line = line
        self.curie = curie    # prefixed spelling, when written that way
        self.lang = lang      # language tag of a literal
        self.datatype = None  # datatype IRI of a literal (set by ogit_validate)


class FileParse:
    def __init__(self, path):
        self.path = path
        self.prefixes = {}
        self.statements = []   # (subject Term, [(pred Term, obj Term)])
        self.undeclared = []   # (line, curie)


def parse(path, text, default_prefixes=None):
    """Parse one file. `default_prefixes` resolves prefixes a file uses
    without declaring them, as rdf4j's parser does; import_check passes none."""
    fp = FileParse(path)
    defaults = default_prefixes or {}
    toks = list(tokenize(text))
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else (None, None, None)

    def take(kind=None):
        nonlocal pos
        if pos >= len(toks):
            raise ParseError("unexpected end of file")
        tok = toks[pos]
        if kind is not None and tok[0] != kind:
            raise ParseError(f"line {tok[2]}: expected {kind!r}, got {tok[1]!r}")
        pos += 1
        return tok

    def resolve(word, line):
        if word == "a":
            return Term("iri", RDF_TYPE, line, "a")
        if word in ("true", "false") or re.fullmatch(r"[+-]?\d+(\.\d+)?", word):
            t = Term("lit", word, line)
            kind = "boolean" if word in ("true", "false") else "decimal" if "." in word else "integer"
            t.datatype = "http://www.w3.org/2001/XMLSchema#" + kind
            return t
        if ":" not in word:
            raise ParseError(f"line {line}: bare word {word!r}")
        prefix, local = word.split(":", 1)
        if prefix not in fp.prefixes and prefix in defaults:
            return Term("iri", defaults[prefix] + local, line, word)
        if prefix not in fp.prefixes:
            fp.undeclared.append((line, word))
            return Term("iri", "urn:undeclared:" + word, line, word)
        return Term("iri", fp.prefixes[prefix] + local, line, word)

    def obj():
        kind, value, line = peek()
        if kind == "iri":
            take()
            return Term("iri", value, line)
        if kind == "lit":
            take()
            return Term("lit", value[0], line, lang=value[1])
        if kind == "word":
            take()
            return resolve(value, line)
        if kind == "(":
            take()
            items = []
            while peek()[0] != ")":
                items.append(obj())
            take(")")
            return Term("list", items, line)
        if kind == "[":
            take()
            pairs = pred_obj_list(close="]")
            take("]")
            return Term("bnode", pairs, line)
        raise ParseError(f"line {line}: unexpected {value!r}")

    def pred_obj_list(close):
        pairs = []
        while True:
            kind = peek()[0]
            if kind in (close, None):
                return pairs
            if kind == ";":
                take()
                continue
            pkind, pvalue, pline = take()
            if pkind == "iri":
                pred = Term("iri", pvalue, pline)
            elif pkind == "word":
                pred = resolve(pvalue, pline)
            else:
                raise ParseError(f"line {pline}: expected a predicate, got {pvalue!r}")
            pairs.append((pred, obj()))
            while peek()[0] == ",":
                take()
                pairs.append((pred, obj()))
            if peek()[0] not in (";", close):
                raise ParseError(f"line {peek()[2]}: expected ';' or {close!r}, got {peek()[1]!r}")

    while pos < len(toks):
        kind, value, line = peek()
        if kind == "word" and value in ("@prefix", "@PREFIX"):
            take()
            _, name, pline = take("word")
            if not name.endswith(":"):
                raise ParseError(f"line {pline}: bad prefix name {name!r}")
            _, iri, _ = take("iri")
            fp.prefixes[name[:-1]] = iri
            take(".")
            continue
        subject = obj()
        pairs = pred_obj_list(close=".")
        take(".")
        fp.statements.append((subject, pairs))
    return fp


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def curie_of(iri, prefixes):
    """Spell an IRI with the repository's prefix convention."""
    if iri.startswith("urn:undeclared:"):
        return iri[len("urn:undeclared:"):]
    best = None
    for name, base in prefixes.items():
        if iri.startswith(base) and (best is None or len(base) > len(prefixes[best])):
            best = name
    if best is None:
        return "<" + iri + ">"
    return best + ":" + iri[len(prefixes[best]):]


def local_name(iri):
    return re.split(r"[/#]", iri)[-1]


def build(parses):
    decls = defaultdict(list)          # iri -> [(path, {pred: [obj]})]
    prefixes = {}
    for fp in parses:
        for name, base in fp.prefixes.items():
            prefixes.setdefault(name, base)
        for subject, pairs in fp.statements:
            if subject.kind != "iri":
                continue
            props = defaultdict(list)
            for pred, o in pairs:
                props[pred.value].append(o)
            decls[subject.value].append((fp.path, props, subject.line))
    return decls, prefixes


def iris(objs):
    return {o.value for o in objs if o.kind == "iri"}


def classify(decls):
    types = {iri: set().union(*(iris(p.get(RDF_TYPE, [])) for _, p, _ in ds))
             for iri, ds in decls.items()}
    supers = {iri: set().union(*(iris(p.get(SUBCLASS, [])) for _, p, _ in ds))
              for iri, ds in decls.items()}
    subprops = {iri: set().union(*(iris(p.get(SUBPROP, [])) for _, p, _ in ds))
                for iri, ds in decls.items()}

    classes = {iri for iri, t in types.items() if RDFS_CLASS in t}

    def reaches(iri, root, seen=None):
        seen = seen or set()
        for s in supers.get(iri, ()):
            if s == root:
                return True
            if s not in seen and s in classes:
                seen.add(s)
                if reaches(s, root, seen):
                    return True
        return False

    entities = {iri for iri in classes if iri != ENTITY and reaches(iri, ENTITY)}
    property_verbs = {iri for iri, sp in subprops.items() if VERB in sp}
    class_verbs = {iri for iri in classes if reaches(iri, VERB)}
    return types, supers, classes, entities, property_verbs, class_verbs, reaches


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def run(root, files):
    findings = []      # (code, path, line, message)
    parses = []
    for path in files:
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            fp = parse(rel, text)
        except (ParseError, UnicodeDecodeError) as exc:
            return None, [("PARSE", rel, 0, str(exc))]
        for line, curie in fp.undeclared:
            findings.append(("C01", rel, line, f"prefix of {curie} is not declared in this file"))
        parses.append(fp)

    decls, prefixes = build(parses)
    types, supers, classes, entities, pverbs, cverbs, reaches = classify(decls)
    verbs = pverbs | cverbs
    base_classes = {iri for iri in classes if iri.startswith(OGIT) and "/" not in iri[len(OGIT):]}

    def c(iri):
        return curie_of(iri, prefixes)

    # C07, C03, C02, C08
    for iri, ds in decls.items():
        if iri.startswith("urn:undeclared:"):
            continue
        if len(ds) > 1:
            where = ", ".join(p for p, _, _ in ds)
            findings.append(("C07", ds[0][0], ds[0][2], f"{c(iri)} is declared in {len(ds)} files: {where}"))
        for path, props, line in ds:
            stem = os.path.splitext(os.path.basename(path))[0]
            if os.path.basename(path) != "ogit.ttl" and local_name(iri) != stem:
                findings.append(("C03", path, line, f"{c(iri)} is declared in {os.path.basename(path)}"))
        if iri in cverbs and not any(p.get(FROM_TO) for _, p, _ in ds):
            findings.append(("C02", ds[0][0], ds[0][2],
                             f"{c(iri)} is an rdfs:Class under ogit:Verb without an ogit:from-to list"))
        elif iri in pverbs and OBJECT_PROPERTY not in types.get(iri, ()):
            findings.append(("C02", ds[0][0], ds[0][2],
                             f"{c(iri)} is a sub-property of ogit:Verb but not an owl:ObjectProperty"))
        if iri in classes and iri not in base_classes and iri not in entities and iri not in cverbs:
            if not any(reaches(iri, b) for b in base_classes):
                missing = sorted(c(s) for s in supers.get(iri, ()) if s not in classes)
                why = f" (super class {', '.join(missing)} is not declared)" if missing else ""
                findings.append(("C08", ds[0][0], ds[0][2],
                                 f"class {c(iri)} does not reach ogit:Entity or another OGIT base class{why}"))

    # Edges: ogit:allowed on entities, ogit:from-to on verbs.
    edges = []        # (src, verb, dst, path, line, origin)
    for iri, ds in decls.items():
        for path, props, line in ds:
            for lst in props.get(ALLOWED, []):
                cells = lst.value if lst.kind == "list" else [lst]
                for cell in cells:
                    if cell.kind != "bnode" or len(cell.value) != 1:
                        findings.append(("C06", path, cell.line,
                                         f"allowed cell of {c(iri)} is not one [ verb target ] pair"))
                        continue
                    verb, dst = cell.value[0]
                    if dst.kind != "iri":
                        findings.append(("C06", path, cell.line,
                                         f"allowed target of {c(iri)} via {c(verb.value)} is not a term"))
                        continue
                    edges.append((iri, verb.value, dst.value, path, cell.line, "allowed"))
            for lst in props.get(FROM_TO, []):
                cells = lst.value if lst.kind == "list" else [lst]
                for cell in cells:
                    froms = [o for p, o in cell.value if p.value == FROM] if cell.kind == "bnode" else []
                    tos = [o for p, o in cell.value if p.value == TO] if cell.kind == "bnode" else []
                    if len(froms) != 1 or len(tos) != 1 or froms[0].kind != "iri" or tos[0].kind != "iri":
                        findings.append(("C06", path, cell.line,
                                         f"from-to cell of {c(iri)} is not one [ ogit:from X ; ogit:to Y ]"))
                        continue
                    edges.append((froms[0].value, iri, tos[0].value, path, cell.line, "from-to"))

    for src, verb, dst, path, line, origin in edges:
        if verb not in verbs:
            findings.append(("C04", path, line, f"verb {c(verb)} ({origin} of {c(src)}) is not declared"))
        for end, label in ((src, "source"), (dst, "target")):
            if origin == "allowed" and label == "source":
                continue
            if end not in entities:
                findings.append(("C05", path, line,
                                 f"{label} {c(end)} of {c(verb)} ({origin}) is not a declared entity"))

    model = {
        "decls": decls, "prefixes": prefixes, "verbs": verbs, "pverbs": pverbs,
        "cverbs": cverbs, "types": types, "edges": edges, "entities": entities,
    }
    return model, findings


def write_verbs(path, model):
    c = lambda iri: curie_of(iri, model["prefixes"])
    uses = defaultdict(int)
    for _, verb, _, _, _, _ in model["edges"]:
        uses[verb] += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("verb\tfile\tshape\tuses\n")
        for verb in sorted(set(model["verbs"]) | set(uses), key=c):
            ds = model["decls"].get(verb)
            file = ds[0][0] if ds else ""
            if verb in model["cverbs"]:
                shape = "class"
            elif verb in model["pverbs"] and OBJECT_PROPERTY in model["types"].get(verb, ()):
                shape = "object-property"
            elif verb in model["pverbs"]:
                shape = "property"
            else:
                shape = "undeclared"
            fh.write(f"{c(verb)}\t{file}\t{shape}\t{uses[verb]}\n")


def write_allowed(path, model):
    c = lambda iri: curie_of(iri, model["prefixes"])
    rows = sorted({(c(s), c(v), c(d), p, o) for s, v, d, p, _, o in model["edges"]})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("source\tverb\ttarget\tfile\torigin\n")
        for row in rows:
            fh.write("\t".join(row) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    ap.add_argument("--warn", action="append", default=[], metavar="CODE",
                    choices=ALL_CODES, help="report CODE without failing (repeatable)")
    ap.add_argument("--emit-verbs", metavar="TSV", help="write the verb catalogue")
    ap.add_argument("--emit-allowed", metavar="TSV", help="write the (source, verb, target) table")
    ap.add_argument("--exclude", action="append", default=[], metavar="PATH",
                    help="skip files under PATH, relative to root (repeatable)")
    ap.add_argument("--quiet", action="store_true", help="print only the summary")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel_dir = os.path.relpath(dirpath, root)
        if any(rel_dir == e or rel_dir.startswith(e.rstrip("/") + os.sep)
               for e in (os.path.normpath(x) for x in args.exclude)):
            continue
        for name in sorted(filenames):
            if name.endswith(".ttl") and not name.startswith("graphit-ontology."):
                files.append(os.path.join(dirpath, name))

    model, findings = run(root, files)
    if model is None:
        for code, path, line, msg in findings:
            print(f"{path}: {code}: {msg}", file=sys.stderr)
        return 2

    findings.sort(key=lambda f: (f[0], f[1], f[2]))
    counts = defaultdict(int)
    for code, path, line, msg in findings:
        counts[code] += 1
        if not args.quiet:
            level = "warning" if code in args.warn else "error"
            print(f"{path}:{line}: {level} {code}: {msg}")

    if args.emit_verbs:
        write_verbs(args.emit_verbs, model)
    if args.emit_allowed:
        write_allowed(args.emit_allowed, model)

    errors = sum(n for code, n in counts.items() if code not in args.warn)
    summary = ", ".join(f"{code}={counts[code]}" for code in ALL_CODES if counts[code])
    print(f"import_check: {len(files)} files, {len(model['entities'])} entities, "
          f"{len(model['verbs'])} verbs, {len(model['edges'])} edges; "
          f"{errors} error(s){'; ' + summary if summary else ''}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
