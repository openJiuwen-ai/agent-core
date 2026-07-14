# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import os
from typing import Any, List, Dict

from json_repair import repair_json
from pyoxigraph import Store, RdfFormat, NamedNode

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor


class OntologyTripleExtractor(Extractor):
    """Ontology based triple extractor implementation using LLM"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        ontology_name: str,
        ontology_path: str | None = None,
        constrain_ontology: bool = False,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        **kwargs,
          
    ):
        """
        Initialize ontology based triple extractor

        Args:
            llm_client: LLM client instance
            model_name: Model name
            ontology_name: Name of the ontology
            ontology_path: Path to the file where the ontology is stored. 
                It can be either a [.nt file](https://www.w3.org/TR/rdf12-n-triples/) 
                or a [.ttl file](https://www.w3.org/TR/turtle/)
            constrain_ontology: Enforce ontology rules for extracted triples
            temperature: Temperature parameter
            max_concurrent: Maximum concurrency, defaults to 50
        """
                
        self.llm_client = llm_client
        self.model_name = model_name
        self.ontology_name = ontology_name
        self.ontology_path = ontology_path
        self.temperature = temperature
        self.limiter = asyncio.Semaphore(max_concurrent)

        self.constrain_ontology = constrain_ontology

        self.ontology_classes = []
        self.ontology_properties = []

        self.class_info = {}
        self.store = None

        if ontology_path:
            valid_extensions = (".nt", ".ttl")
            if not ontology_path.lower().endswith(valid_extensions):
                raise build_error(
                    StatusCode.RETRIEVAL_INDEXING_FORMAT_NOT_SUPPORT,
                    error_msg=f"{ontology_path}: Ontology file must be either .nt or .ttl",
                )
            
            if not os.path.isfile(ontology_path):
                raise build_error(
                    StatusCode.RETRIEVAL_INDEXING_FILE_NOT_FOUND,
                    error_msg=f"Ontology file does not exist: {ontology_path}"
                )

            self._load_ontology()
        else:
            if self.constrain_ontology:
                logger.warning(f"Cannot constrain ontology with no ontology_path")
                self.constrain_ontology = False


    async def extract(self, chunks: List[TextChunk], **kwargs) -> List[Triple]:
        """
        Extract ontology based triples from chunks via parallel LLM calls.

        On any failure, raises the first error in chunk order (``BaseError`` is re-raised
        unchanged; other exceptions are wrapped).

        Args:
            chunks: Text chunks to process.
            **kwargs: Reserved for extractor API compatibility.

        Returns:
            All triples merged from successful chunk results.

        Raises:
            BaseError: Including ``RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR`` when
                extraction or parsing fails.
        """
        async def process(chunk: TextChunk):
            """
            Invoke LLM for entity and triple extraction for a single chunk.

            Args:
                chunk: Text chunk to process.

            Returns:
                Extracted triples for this chunk.
            """
            async with self.limiter:
                try:
                    entities = await self._extract_entities(chunk)
                    triples = await self._extract_relations(chunk, entities)
                    return triples

                except BaseError:
                    raise
                except Exception as e:
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                        error_msg=f"{chunk.id_}: {e}",
                        cause=e,
                    ) from e

        tasks = [asyncio.create_task(process(c)) for c in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_triples = []
        first_error = None

        for r in results:
            if isinstance(r, Exception):
                if not first_error:
                    first_error = r
                continue
            all_triples.extend(r)

        if first_error:
            raise first_error

        return all_triples


    async def _extract_entities(self, chunk: TextChunk) -> List[Dict]:
        """
        Performs entity recognition for the given chunks.

        Args:
            chunks: Text chunks from which to extract entities.

        Returns:
            A list of entities extracted from the chunk in the format {uri, label, class}.
        """
        prompt = self._build_entity_prompt(
            chunk.text,
            chunk.metadata.get("title", "")
        )

        completion = await self.llm_client.invoke(
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )

        entities, success = self._parse_entities(completion.content)

        if not success:
            raise build_error(
                StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                error_msg=f"{chunk.id_}: entity extraction failed",
            )

        return entities

    def _build_entity_prompt(self, passage: str, title: str = "") -> str:
        """
        Build the ontology-based entity extraction prompt for the LLM.

        Args:
            passage: Chunk body text.
            title: Optional document or section title from chunk metadata.

        Returns:
            Rendered prompt string.
        """
        ontology_section = "Given Ontology: " + self.ontology_name + " "

        if self.ontology_classes:
            if self.constrain_ontology and self.class_info:
                enriched_lines = []
                for c in self.ontology_classes:
                    info = self.class_info.get(c, {})
                    subclass = info.get("subclassof", "None")
                    comment = info.get("comment", "")
                    short_desc = " ".join(comment.split()[:20])

                    enriched_lines.append(
                        f"{c} subclassof: {subclass}\n{short_desc}"
                    )

                ontology_section += f"""
Allowed Classes:
{chr(10).join(enriched_lines)}

Use ONLY these classes when possible.
"""
            else:
                ontology_section += f"""
Allowed Classes:
{chr(10).join("- " + c for c in self.ontology_classes)}

Use ONLY these classes when possible.
"""

        return f"""
please find all the entities  (including inferred ones) for the following text using the given ontology classes.

Return JSON:
{{
  "entities": [
    {{
      "uri": "uri",
      "label": "human readable name",
      "class": "ontology_class"
    }}
  ]
}}

Rules:
- Please be as precise as you can by extracting as much entities as you can, there is no need to include the superclasses for each entity.
- Each entity MUST have a uri (its suffix contains the label), a label, and a  class
- Use ontology classes if provided
- Do not use prefixes for uris, classes
- No duplicates
- JSON only

{ontology_section}

Title:
{title or "Untitled"}

Passage:
{passage}
"""

    def _parse_entities(self, content: str) -> tuple[List[Dict], bool]:
        """
        Parse LLM output into {uri, label, class} objects.

        Args:
            content: Raw model output (JSON or markdown-fenced JSON).

        Returns:
            ``(entities, parse_success)``: ``parse_success`` is True when JSON was valid
            and structure was acceptable (empty entity list allowed); False on hard parse errors.
        """
                
        try:
            content = content.strip()
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:-1])

            parsed = repair_json(content, return_objects=True)

            if not isinstance(parsed, dict):
                return [], False

            entities = parsed.get("entities")
            if not isinstance(entities, list):
                return [], False

            clean = []
            for e in entities:
                if not isinstance(e, dict):
                    continue
                if not all(k in e for k in ("uri", "label", "class")):
                    continue
                clean.append({
                    "uri": str(e["uri"]).strip(),
                    "label": str(e["label"]).strip(),
                    "class": str(e["class"]).strip(),
                })

            return clean, True

        except Exception as e:
            logger.error(f"Entity parsing failed: {e}")
            return [], False


    async def _extract_relations(self, chunk: TextChunk, entities: List[Dict]) -> List[Triple]:
        """
        Performs relation extraction for the given chunks.

        Args:
            chunks: Text chunks from which to extract relations.
            entities: Entities to be used in relation extraction

        Returns:
            All extracted relations as triples
        """
        if self.constrain_ontology:
            valid_props = self._get_valid_properties(entities)
            property_restriction = "\n".join(
                f"- {p['property']} (domain={p['domain']}, range={p['range']})"
                for p in valid_props
            )
        else:
            property_restriction = ""
        prompt = self._build_relation_prompt(
            chunk.text,
            entities,
            property_restriction,
            chunk.metadata.get("title", ""),
        )

        completion = await self.llm_client.invoke(
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )

        triples, success = self._parse_triples(
            completion.content,
            chunk.doc_id,
            chunk.id_
        )

        if not success:
            raise build_error(
                StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                error_msg=f"{chunk.id_}: relation extraction failed",
            )

        return triples

    def _build_relation_prompt(
            self, 
            passage: str, 
            entities: List[Dict], 
            property_restriction: str = "", 
            title: str = "") -> str:
        """
        Build the ontology-based relation extraction prompt for the LLM.

        Args:
            passage: Chunk body text.
            entities: Entities that were extracted from the passage.
            property_restriction: Optional string of property restrictions
            title: Optional document or section title from chunk metadata.

        Returns:
            Rendered prompt string.
        """
        entity_block = "\n".join(
            f"- {e['uri']} (label={e['label']}, class={e['class']})"
            for e in entities
        )

        ontology_section = f"Ontology: {self.ontology_name}"

        if self.constrain_ontology:
            ontology_section += f"""

            Candidate Properties (with domain and range):
            {property_restriction}

            Use ONLY the relevant properties
            """
        
        elif self.ontology_properties:
            ontology_section += f"""

             Candidate Properties:
            {chr(10).join("- " + p for p in self.ontology_properties)}

            Use ONLY the relevant properties
            """

        return f"""


Please produce  all the  relationships  (including inferred ones from the text) and then the triples of the text"
between the entities by using the same URIs for the entities "
                      
Entities:
{entity_block}

Return JSON:
{{
  "triples": [
    ["subject", "predicate", "object (entities or literals)"]
  ]
}}

Rules:
- Include rdf:type triples using the provided classes (as objects)
- Use the ontology properties as predicates
- Do not use prefixes for the properties
- JSON only
{ontology_section}

Title:
{title or "Untitled"}

Passage:
{passage}
"""

    def _parse_triples(self, content: str, doc_id: str, chunk_id: str) -> tuple[List[Triple], bool]:
        """
        Parse LLM output into ``Triple`` objects.

        Args:
            content: Raw model output (JSON or markdown-fenced JSON).
            doc_id: Document id for triple metadata.
            chunk_id: Chunk id for triple metadata.

        Returns:
            ``(triples, parse_success)``: ``parse_success`` is True when JSON was valid
            and structure was acceptable (empty triple list allowed); False on hard parse errors.
        """
        triples = []

        try:
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

            try:
                parsed = repair_json(content, return_objects=True)
            except Exception as e:
                logger.error("Failed to parse triples from content: %s. Content: %s", e, content[:200])
                return [], False

            if isinstance(parsed, dict):
                if "triples" not in parsed or not isinstance(parsed.get("triples"), list):
                    return [], False
                triple_list = parsed["triples"]
            elif isinstance(parsed, list):
                triple_list = parsed
            else:
                return [], False

            if not triple_list:
                return [], True

            invalid_count = 0
            for triple_data in triple_list:
                if not isinstance(triple_data, (list, tuple)):
                    invalid_count += 1
                    continue
                if len(triple_data) < 3:
                    invalid_count += 1
                    continue

                head = triple_data[:3]
                if any(isinstance(x, (list, tuple, dict)) or x is None for x in head):
                    invalid_count += 1
                    continue

                triples.append(
                    Triple(
                        subject=str(head[0]).strip(),
                        predicate=str(head[1]).strip(),
                        object=str(head[2]).strip(),
                        metadata={"doc_id": doc_id, "chunk_id": chunk_id},
                    )
                )

            if invalid_count:
                logger.warning(
                    "Ignored %d invalid triples for chunk %s during parsing",
                    invalid_count,
                    chunk_id,
                )

            return triples, bool(triples)

        except Exception as e:
            logger.error("Failed to parse triples: %s", e)
            return [], False

    def _load_ontology(self):
        """
        Loads an ontology from a file, sets the ontology classes and 
        properties that can be used by the LLM and pre-computes subclass 
        relationships.
        """
        try:
            self.store = Store()
            if self.ontology_path.lower().endswith(".nt"):
                with open(self.ontology_path, "rb") as f:
                    self.store.load(f, format=RdfFormat.N_TRIPLES)
            elif self.ontology_path.lower().endswith(".ttl"):
                with open(self.ontology_path, "rb") as f:
                    self.store.load(f, format=RdfFormat.TURTLE)

            classes = set()
            properties = set()

            class_query = """
            SELECT ?c WHERE {
                { ?c a <http://www.w3.org/2000/01/rdf-schema#Class> }
                UNION
                { ?c a <http://www.w3.org/2002/07/owl#Class> }
            }
            """

            class_info_query = """
            SELECT ?c ?parent ?comment WHERE {
                { ?c a <http://www.w3.org/2000/01/rdf-schema#Class> }
                UNION
                { ?c a <http://www.w3.org/2002/07/owl#Class> }
                OPTIONAL { ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf> ?parent }
                OPTIONAL { ?c <http://www.w3.org/2000/01/rdf-schema#comment> ?comment }
            }
            """

            prop_query = """
            SELECT ?p WHERE {
                { ?p a <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property> }
                UNION
                { ?p a <http://www.w3.org/2002/07/owl#ObjectProperty> }
                UNION
                { ?p a <http://www.w3.org/2002/07/owl#DatatypeProperty> }
            }
            """
            
            for row in self.store.query(class_query):
                classes.add(self.get_suffix(row["c"]))

            for row in self.store.query(class_info_query):
                c = self.get_suffix(row["c"])
                parent = self.get_suffix(row["parent"]) if row["parent"] else "None"
                comment = str(row["comment"]) if row["comment"] else ""

                self.class_info[c] = {
                    "subclassof": parent,
                    "comment": comment
                }

            for row in self.store.query(prop_query):
                properties.add(self.get_suffix(row["p"]))

            self.ontology_classes = sorted(classes)
            self.ontology_properties = sorted(properties)

            subclass_query = """
            SELECT ?child ?parent WHERE {
                ?child <http://www.w3.org/2000/01/rdf-schema#subClassOf> ?parent .
            }
            """
            self.subclass_map = {}
            for row in self.store.query(subclass_query):
                child = self.get_suffix(row["child"])
                parent = self.get_suffix(row["parent"])
                self.subclass_map.setdefault(child, set()).add(parent)

            self.closure_cache = {}

        except Exception as e:
            logger.warning(f"Failed to load ontology: {e}")
            raise build_error(
                StatusCode.RETRIEVAL_KB_ONTOLOGY_INVALID,
                error_msg=f"Failed to load ontology: {e}",
                cause=e,
            ) from e

    def shorten(self, uri: str) -> str:
        return uri.split("#")[-1].split("/")[-1].replace(">", "")

    def get_suffix(self, node) -> str:
        """
        Get node suffix

        Args:
            node: pyoxigraph node
        Returns:
            The node suffix
        """
        if isinstance(node, NamedNode):
            uri = node.value
            if "#" in uri:
                return uri.split("#")[-1]
            elif "/" in uri:
                return uri.split("/")[-1]
            return uri
        
        return node.value
   
    def _get_all_superclasses(self, cls_name):
        """
        Computes the transitive closure of all superclasses for a given class name.
        Traverses the subclass hierarchy using DFS search.

        Args:
            cls_name: The name of the class to evaluate.

        Returns:
            A set of all superclass names for the provided class.
        """

        if cls_name in self.closure_cache:
            return self.closure_cache[cls_name]

        visited = set()
        stack = list(self.subclass_map.get(cls_name, set()))

        while stack:
            current = stack.pop()
            if current not in visited:
                visited.add(current)
                stack.extend(self.subclass_map.get(current, set()))

        self.closure_cache[cls_name] = visited
        return visited
        
    def _get_valid_properties(self, entities: List[Dict]) -> List[Dict]:
        """
        Calculate the valid properties that the LLM can use
        
        Returns:
            The valid properties that can be used by the LLM in the format {property, domain, range}
        """
        try:
            # Collect entity classes
            entity_classes = {
                e["class"].strip()
                for e in entities
                if isinstance(e, dict) and "class" in e
            }

            # Query ontology properties using the pre-loaded store
            query = """
            SELECT ?p ?domain ?range WHERE {
                ?p a ?type .

                FILTER(
                    ?type = <http://www.w3.org/2002/07/owl#ObjectProperty> ||
                    ?type = <http://www.w3.org/2002/07/owl#DatatypeProperty> ||
                    ?type = <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property>
                )

                OPTIONAL {
                    ?p <http://www.w3.org/2000/01/rdf-schema#domain> ?domain
                }

                OPTIONAL {
                    ?p <http://www.w3.org/2000/01/rdf-schema#range> ?range
                }
            }
            """

            valid_props = []
            if not self.store:
                return []

            for row in self.store.query(query):

                p = self.get_suffix(row["p"])

                domain = (
                    self.get_suffix(row["domain"])
                    if row["domain"] else None
                )

                range_ = (
                    self.get_suffix(row["range"])
                    if row["range"] else None
                )

                # Skip properties without domain
                if not domain:
                    continue

                # Domain reasoning
                domain_matches = any(
                    self._is_compatible(entity_cls, domain)
                    for entity_cls in entity_classes
                )

                if not domain_matches:
                    continue

                # Skip properties without range
                if range_ is None:
                    continue

                # Datatype property
                is_literal = (
                    "XMLSchema" in str(row["range"]) or
                    "Literal" in str(row["range"])
                )

                if is_literal:
                    valid_props.append({
                        "property": p,
                        "domain": domain,
                        "range": "Literal"
                    })
                    continue

                # Object property range reasoning
                range_matches = any(
                    self._is_compatible(entity_cls, range_)
                    for entity_cls in entity_classes
                )

                if range_matches:
                    valid_props.append({
                        "property": p,
                        "domain": domain,
                        "range": range_
                    })
                    
            return valid_props

        except Exception as e:
            logger.warning(f"Property filtering failed: {e}")
            return []
        
    def _is_compatible(self, entity_cls: str, ontology_cls: str) -> bool:
        """
        Determines if an extracted entity class is compatible with an ontology class 
        requirement.

        Args:
            entity_cls: The class of the extracted entity.
            ontology_cls: The target ontology class being checked against.

        Returns:
            True if the entity class matches the ontology class exactly or is a 
            valid subclass of it, otherwise False.
        """
        return (
            entity_cls == ontology_cls or
            ontology_cls in self._get_all_superclasses(entity_cls)
        )