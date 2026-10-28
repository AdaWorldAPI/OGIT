# tools

## `ogit_validate.py` — the OGIT validator without Java

`validate.sh` runs `bin/ogit-validator.jar`. `ogit_validate.py` does the same
checks in Python 3.8+ with no packages, and prints the jar's messages on the
same streams with the same exit status. With no arguments it validates the
files `validate.sh` finds from the current directory.

```sh
python3 -I tools/ogit_validate.py
python3 -I tools/ogit_validate.py --no-color --all
python3 -I tools/test_ogit_validate.py
```

The jar runs three stages, each only if the one before passed:

1. Per file (only with more than one file): `ogit*` prefixes must follow the
   `http://www.purl.org/<prefix with / for .>/` convention; the file name
   must be the local name of the file's first subject; and the canonical
   path must contain `/<namespace dir>/…/<name>.ttl`.
2. The ten queries in the jar (`validation0.rq` … `validation9.rq`): required
   properties of entities, attributes and verbs, `ogit:history` items,
   date formats, validation type and parameter.
3. The entity registry: referenced attributes, parents, allowed targets and
   verbs must exist; scopes and cardinalities must be known; labels,
   descriptions, creators and `dcterms:valid` start dates must be present.

Two things decide the output and are easy to miss:

- **The directory check reads the canonical path.** The root-namespace files
  under `SGO/sgo/` and `SGO/core/` pass because the checkout directory is
  named `OGIT`. In a checkout with another name they fail with
  `Directory mismatch`, with the jar and with this port alike.
- **Statements are sorted.** rdf4j's memory store returns statements in
  lexical order, not file order. That decides a file's first subject and the
  order of the stage 3 messages.

Stage 2 reports one subject per failing query. Which one the jar reports
depends on rdf4j's grouping; the port reports the first in store order, so
the subject can differ while the failing queries are the same. `--all` lists
every failing subject.

`test_ogit_validate.py` runs 19 cases and compares the port with output
recorded from the jar; with Java installed it also runs the jar. On this
repository the port and the jar agree in a checkout named `OGIT`, in a
checkout under another name, and on `master`.

## `import_check.py` — strict consistency check for importers

`validate.sh` runs the OGIT validator, which checks what this repository
requires of its own files. `import_check.py` checks what a program that
imports OGIT into another schema needs on top of that, and can write the verb
catalogue and the allowed-edge table that such an import reads.

It is not wired into `validate.sh` or CI and changes no file. It needs only
Python 3.8+ and no packages.

```sh
python3 -I tools/import_check.py
python3 -I tools/import_check.py --emit-verbs verbs.tsv --emit-allowed allowed.tsv
python3 -I tools/import_check.py --exclude NTO/PTF --warn C03
python3 -I tools/test_import_check.py
```

| code | finding |
|---|---|
| C01 | a prefixed name uses a prefix the file does not declare |
| C02 | a verb has neither supported shape: `owl:ObjectProperty` + `rdfs:subPropertyOf ogit:Verb`, or `rdfs:Class` + `rdfs:subClassOf ogit:Verb` with `ogit:from-to` |
| C03 | the file name differs from the local name of the term it declares |
| C04 | an `ogit:allowed` or `ogit:from-to` verb is not declared |
| C05 | an allowed or from-to source/target is not a declared entity |
| C06 | an allowed cell is not one `[ verb target ]` pair |
| C07 | a term is declared in more than one file |
| C08 | a class does not reach `ogit:Entity`, `ogit:Verb` or another OGIT base class |

Exit status is 0 with no errors, 1 with errors, 2 when a file cannot be
parsed. `--warn CODE` reports a check without failing; `--exclude PATH`
skips a directory.

`--emit-verbs` writes `verb, file, shape, uses`, where shape is
`object-property`, `property`, `class` or `undeclared`. `--emit-allowed`
writes `source, verb, target, file, origin`, where origin is `allowed`
(an entity's `ogit:allowed` list) or `from-to` (a verb's `ogit:from-to`
list).
