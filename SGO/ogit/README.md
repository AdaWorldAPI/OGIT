### Overview

This directory contains the terms in the root `ogit:` namespace
(`http://www.purl.org/ogit/`):

* `attributes`, `entities`, `verbs`: the SGO (Semantic Graph Ontology) of OGIT,
  and the ontology objects whose instance nodes and edges describe the
  ontology itself.
* `core`: the core ontology, i.e. concepts on the meta-model level.

They live under a directory named `ogit` because the validator
(`bin/ogit-validator.jar`) requires every file to sit below a directory named
after its namespace: `ogit:creates` must be at `.../ogit/<any>/creates.ttl`.

Please refer to the [OGIT ontology details](https://github.com/almatoai/OGIT/wiki/OGIT-ontology-details) wiki page for an explanation of these concepts.
