# tools

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
| C02 | a verb is not `owl:ObjectProperty` + `rdfs:subPropertyOf ogit:Verb` |
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
