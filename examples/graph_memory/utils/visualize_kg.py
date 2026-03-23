"""
Knowledge Graph Visualization using pyvis
Visualizes the test data from KG_test_data.py as an interactive network graph
"""

import json
import os
from typing import List

from pyvis.network import Network
from pyvis.options import Options

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.graph import Entity, Episode, Relation
from openjiuwen.core.foundation.store.graph.utils import load_stored_time_from_db


def create_kg_visualization(entities: List[Entity], relations: List[Relation], episodes: List[Episode]):
    """Create an interactive knowledge graph visualization using pyvis"""

    # Create a new network
    net = Network(
        height="600px",
        width="100%",
        directed=True,
        bgcolor="#ffffff",
        font_color="#000000",
        cdn_resources="in_line",
        neighborhood_highlight=True,
        select_menu=True,
    )

    # Configure network options
    options = Options()
    options.physics.enabled = True
    options.physics.solver = "forceAtlas2Based"
    options.physics.forceAtlas2Based.gravitationalConstant = -50
    options.physics.forceAtlas2Based.centralGravity = 0.01
    options.physics.forceAtlas2Based.springLength = 100
    options.physics.forceAtlas2Based.springConstant = 0.08
    options.physics.forceAtlas2Based.damping = 0.4
    options.physics.forceAtlas2Based.avoidOverlap = 0.5

    net.options = options

    # Color scheme for different entity types
    entity_colors = {
        "person": "#4285f4",  # Blue
        "place": "#34a853",  # Green
        "project": "#ea4335",  # Red
        "technology": "#fbbc04",  # Yellow
        "hobby": "#9c27b0",  # Purple
        "career_field": "#ff9800",  # Orange
    }

    # Add entities as nodes
    entity_nodes = {}
    entity_uuids = {}
    for entity in entities:
        # Get color based on entity type
        color = entity_colors.get(entity.obj_type, "#666666")

        # Add node
        raw_name = name = entity.name.replace(" ", "_")
        counter = 1
        while name in entity_nodes:
            name = f"{raw_name}-{counter}"
            counter += 1

        # Create node label with type
        node_id = name
        label = f"{name}\n({entity.obj_type})"

        attrs = json.dumps(entity.attributes, ensure_ascii=False, indent=2).replace("\n", "<br>")
        content = entity.content
        obj_type = entity.obj_type
        net.add_node(
            node_id,
            label=label,
            title=f"<b>{name}</b><br>Type: {obj_type}<br>Content: {content}<br>----------<br>Attributes: {attrs}",
            color=color,
            size=25,
            shape="dot",
        )
        entity_uuids[entity.uuid] = name
        entity_nodes[name] = node_id

    # Add episodes as nodes (smaller, different shape)
    episode_nodes = {}
    for episode in episodes:
        # Create node label
        label = f"{episode.uuid}\n({episode.obj_type})"

        # Add node
        node_id = f"episode_{episode.uuid}"
        net.add_node(
            node_id,
            label=label,
            title=f"<b>{episode.uuid}</b><br>Type: {episode.obj_type}<br>Content: {episode.content}",
            color="#e0e0e0",
            size=15,
            shape="diamond",
        )
        episode_nodes[episode.uuid] = node_id

    # Add relations as edges
    for relation in relations:
        try:
            # Get source and target node IDs
            lhs = relation.lhs
            rhs = relation.rhs
            if not isinstance(lhs, str):
                lhs = lhs.uuid
            if not isinstance(rhs, str):
                rhs = rhs.uuid
            source_name = entity_uuids.get(lhs)
            target_name = entity_uuids.get(rhs)
            # Find corresponding nodes
            source_id = None
            target_id = None

            # Check if it's an entity-entity relation
            if source_name in entity_nodes and target_name in entity_nodes:
                source_id = entity_nodes[source_name]
                target_id = entity_nodes[target_name]
            # Check if it's an episode-entity relation (we'll need to create these)
            elif source_name in episode_nodes and target_name in entity_nodes:
                source_id = episode_nodes[source_name]
                target_id = entity_nodes[target_name]
            elif source_name in entity_nodes and target_name in episode_nodes:
                source_id = entity_nodes[source_name]
                target_id = episode_nodes[target_name]
            time_str = []
            if relation.valid_since > 0:
                x = load_stored_time_from_db(relation.valid_since, relation.offset_since).isoformat(timespec="seconds")
                time_str.append(f"Valid since: {x}")
            if relation.valid_until > 0:
                x = load_stored_time_from_db(relation.valid_until, relation.offset_until).isoformat(timespec="seconds")
                time_str.append(f"Valid until: {x}")

            if source_id and target_id:
                # Add edge
                net.add_edge(
                    source_id,
                    target_id,
                    title="<br>".join(
                        [f"<b>{relation.name}</b><br>Type: {relation.obj_type}<br>Content: {relation.content}"]
                        + time_str
                    ),
                    color="#666666",
                    width=2,
                    arrows="to",
                )

        except Exception as e:
            raise e
            # logger.info(f"Warning: Could not add relation {relation.name}: {e}")
            # continue

    # Add some episode-entity connections based on content analysis
    for episode in episodes:
        episode_id = episode_nodes[episode.uuid]

        # Connect episodes to mentioned entities
        for entity in entities:
            if episode.uuid in entity.episodes:
                entity_id = entity_uuids[entity.uuid]
                net.add_edge(
                    episode_id,
                    entity_id,
                    title=f"Mentioned in: {episode.uuid}",
                    color="#cccccc",
                    width=1,
                    arrows="to",
                    dashes=True,
                )

    return net


def save_visualization(net: Network, filename="kg_visualization.html", enable_info_panel=True):
    """Save the visualization to an HTML file

    Args:
        net: Network object from pyvis
        filename: Output filename
        enable_info_panel: If True, adds info panel to the HTML. If False, uses postMessage for external communication.
    """
    try:
        net.set_edge_smooth("dynamic")
        net.save_graph(filename)
        logger.info(f"Visualization saved to {filename}")
        logger.info("Open the HTML file in your web browser to view the interactive graph")
        with open(filename, "r+", encoding="utf-8") as f:
            html = f.read()
            insert_before = "</body>"

            if enable_info_panel:
                # Original info panel implementation
                patch = """<style>
                #info-panel {
                    position: fixed;
                    right: 0;
                    top: 0;
                    width: 15%;
                    height: 100%;
                    background-color: #f9f9f9;
                    border-left: 1px solid #ccc;
                    padding: 15px;
                    overflow-y: auto;
                    font-family: Arial, sans-serif;
                    font-size: 14px;
                    white-space: normal;
                    word-break: break-word;
                    z-index: 9999; /* ensure it's above the canvas */
                }

                #info-panel h3 {
                    margin-top: 0;
                }

                #info-content pre {
                    white-space: pre-wrap !important;    /* keep newlines + wrap long lines */
                    word-break: break-word !important;
                    overflow-wrap: anywhere !important;
                    text-overflow: visible !important;      /* no ellipsis */
                    overflow: visible !important;        /* allow full text */
                    display: block;
                    font-family: inherit;
                    margin: 0;
                }

                /* Just to be safe: override any PyVis global styles */
                div, span, p {
                    text-overflow: visible !important;
                }
                </style>

                <div id="info-panel">
                <h3>Details</h3>
                <div id="info-content">Click a node or relation to view details</div>
                </div>
                <script type="text/javascript">
                network.on("click", function (params) {
                const infoContent = document.getElementById("info-content");
                if (params.nodes.length > 0) {
                    const node = nodes.get(params.nodes[0]);
                    infoContent.innerHTML = "<strong>Entity:</strong><br><pre>" + node.title + "</pre>";
                } else if (params.edges.length > 0) {
                    const edge = edges.get(params.edges[0]);
                    infoContent.innerHTML = "<strong>Relation:</strong><br><pre>" + edge.title + "</pre>";
                } else {
                    infoContent.textContent = "Click a node or relation to view details";
                }
                });
                </script>"""
            else:
                # Use postMessage to communicate with parent window
                patch = """<script type="text/javascript">
                network.on("click", function (params) {
                    if (window.parent && window.parent !== window) {
                        if (params.nodes.length > 0) {
                            const node = nodes.get(params.nodes[0]);
                            window.parent.postMessage({
                                type: 'node-click',
                                data: {
                                    label: node.label || node.id,
                                    title: node.title,
                                    id: node.id
                                }
                            }, '*');
                        } else if (params.edges.length > 0) {
                            const edge = edges.get(params.edges[0]);
                            window.parent.postMessage({
                                type: 'edge-click',
                                data: {
                                    label: edge.label || edge.id,
                                    title: edge.title,
                                    id: edge.id,
                                    from: edge.from,
                                    to: edge.to
                                }
                            }, '*');
                        } else {
                            window.parent.postMessage({
                                type: 'clear-selection'
                            }, '*');
                        }
                    }
                });
                </script>"""

            f.seek(0)
            f.write(html.replace(insert_before, patch + insert_before))
            f.truncate()
    except Exception:
        with open(filename, "w", encoding="utf-8") as f:
            f.write("")


def main(entities: List[Entity], relations: List[Relation], episodes: List[Episode], name: str):
    """Main function to create and save the visualization"""
    logger.info("Creating Knowledge Graph visualization...")
    name = name.removesuffix(".html")
    folder_path = os.path.dirname(name)
    if folder_path:
        os.makedirs(folder_path, exist_ok=True)

    # Create the visualization
    if entities and relations and episodes:
        net = create_kg_visualization(entities, relations, episodes)
        save_visualization(net, filename=f"{name}.html")

    # Print some statistics
    logger.info("Visualization created with:")
    logger.info(f"- {len(entities)} entities")
    logger.info(f"- {len(episodes)} episodes")
    logger.info(f"- {len(relations)} relations")
    logger.info(f"- {len(net.nodes)} total nodes")
    logger.info(f"- {len(net.edges)} total edges")
