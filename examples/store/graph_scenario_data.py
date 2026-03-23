# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Graph scenario data: Entity, Relation, and Episode definitions for MilvusGraphStore demos.

This module defines a single rich scenario (e.g. org + projects) so the showcase
can import and use it without inlining all definitions. Entities are linked to
relations via Relation.update_connected_entities() so that stored entities
correctly reference the relations they participate in.
"""

from openjiuwen.core.foundation.store.graph import Entity, Episode, Relation

USER_ID = "test_user"
LANG = "en"


def build_large_scenario_with_irrelevant_data():
    """
    Build a large scenario with a clear relevant subgraph (ML/Search) and many
    irrelevant entities (HR, Finance, Sales, etc.) for testing BFS graph expansion.

    The relevant subgraph: Alice, Bob, Carol, Dave, Eve + Project Search, Project ML Platform
    and their relations. BFS from top-k semantic hits (e.g. "ML and NLP") should expand
    only within this subgraph; irrelevant people/projects have no edges to it.

    Returns:
        tuple: (entities, relations, episodes) with entities having their
               relations list populated.
    """
    # --- Relevant subgraph: ML/Search org (same as build_company_project_scenario) ---
    alice = Entity(
        name="Alice",
        content="Alice leads the ML platform team. She works on model training pipelines and NLP tooling.",
        language=LANG,
        user_id=USER_ID,
    )
    bob = Entity(
        name="Bob",
        content="Bob is a backend engineer focused on distributed systems, APIs and database reliability.",
        language=LANG,
        user_id=USER_ID,
    )
    carol = Entity(
        name="Carol",
        content="Carol leads the product team and user research. She owns the roadmap for Search and Discovery.",
        language=LANG,
        user_id=USER_ID,
    )
    dave = Entity(
        name="Dave",
        content="Dave is a senior ML engineer. He implements recommendation models and A/B experiments.",
        language=LANG,
        user_id=USER_ID,
    )
    eve = Entity(
        name="Eve",
        content="Eve is the engineering manager for Search. She coordinates backend and ML for search ranking.",
        language=LANG,
        user_id=USER_ID,
    )
    project_search = Entity(
        name="Project Search",
        content="Search and Discovery: unified search API, ranking models, and relevance metrics.",
        language=LANG,
        user_id=USER_ID,
    )
    project_ml_platform = Entity(
        name="Project ML Platform",
        content="ML Platform: training pipelines, feature store, and model serving infrastructure.",
        language=LANG,
        user_id=USER_ID,
    )

    relevant_entities = [alice, bob, carol, dave, eve, project_search, project_ml_platform]

    # --- Irrelevant: HR, Finance, Sales, Marketing, Legal, Support, etc. ---
    frank = Entity(
        name="Frank",
        content="Frank heads HR. He handles recruitment, onboarding, and employee policies.",
        language=LANG,
        user_id=USER_ID,
    )
    grace = Entity(
        name="Grace",
        content="Grace is in Finance. She manages budgets, forecasting, and financial reporting.",
        language=LANG,
        user_id=USER_ID,
    )
    henry = Entity(
        name="Henry",
        content="Henry leads Sales. He focuses on enterprise deals and customer contracts.",
        language=LANG,
        user_id=USER_ID,
    )
    irene = Entity(
        name="Irene",
        content="Irene runs Marketing. She oversees campaigns, brand, and demand generation.",
        language=LANG,
        user_id=USER_ID,
    )
    jack = Entity(
        name="Jack",
        content="Jack is Legal counsel. He handles contracts, compliance, and IP.",
        language=LANG,
        user_id=USER_ID,
    )
    kate = Entity(
        name="Kate",
        content="Kate leads Customer Support. She manages tickets, SLAs, and support tooling.",
        language=LANG,
        user_id=USER_ID,
    )
    leo = Entity(
        name="Leo",
        content="Leo is in DevOps. He maintains CI/CD, cloud infrastructure, and monitoring.",
        language=LANG,
        user_id=USER_ID,
    )
    maria = Entity(
        name="Maria",
        content="Maria is QA lead. She runs test automation and release quality checks.",
        language=LANG,
        user_id=USER_ID,
    )
    nick = Entity(
        name="Nick",
        content="Nick is a designer. He works on UI/UX for internal tools and dashboards.",
        language=LANG,
        user_id=USER_ID,
    )
    olivia = Entity(
        name="Olivia",
        content="Olivia is in HR. She handles benefits, payroll, and workplace policies.",
        language=LANG,
        user_id=USER_ID,
    )
    paul = Entity(
        name="Paul",
        content="Paul is in Sales. He manages SMB accounts and outbound pipeline.",
        language=LANG,
        user_id=USER_ID,
    )
    quinn = Entity(
        name="Quinn",
        content="Quinn is in Finance. She handles accounts payable and vendor contracts.",
        language=LANG,
        user_id=USER_ID,
    )
    rachel = Entity(
        name="Rachel",
        content="Rachel is in Marketing. She runs content and social media.",
        language=LANG,
        user_id=USER_ID,
    )
    sam = Entity(
        name="Sam",
        content="Sam is Legal. He focuses on data privacy and GDPR compliance.",
        language=LANG,
        user_id=USER_ID,
    )
    tina = Entity(
        name="Tina",
        content="Tina is in Support. She handles tier-2 escalations and knowledge base.",
        language=LANG,
        user_id=USER_ID,
    )

    # Irrelevant projects (no link to ML/NLP/Search)
    project_hr = Entity(
        name="Project HR Portal",
        content="HR Portal: recruitment, onboarding, leave, and policy documents.",
        language=LANG,
        user_id=USER_ID,
    )
    project_finance = Entity(
        name="Project Finance Dashboard",
        content="Finance Dashboard: budgets, P&L, and financial reporting.",
        language=LANG,
        user_id=USER_ID,
    )
    project_sales = Entity(
        name="Project Sales CRM",
        content="Sales CRM: pipeline, contacts, and deal tracking.",
        language=LANG,
        user_id=USER_ID,
    )
    project_marketing = Entity(
        name="Project Marketing Campaigns",
        content="Marketing: campaign tracking, leads, and analytics.",
        language=LANG,
        user_id=USER_ID,
    )
    project_legal = Entity(
        name="Project Legal Repository",
        content="Legal: contract templates, compliance docs, and IP records.",
        language=LANG,
        user_id=USER_ID,
    )
    project_support = Entity(
        name="Project Support Tooling",
        content="Support: ticketing, knowledge base, and SLA dashboards.",
        language=LANG,
        user_id=USER_ID,
    )

    irrelevant_people = [
        frank,
        grace,
        henry,
        irene,
        jack,
        kate,
        leo,
        maria,
        nick,
        olivia,
        paul,
        quinn,
        rachel,
        sam,
        tina,
    ]
    irrelevant_projects = [
        project_hr,
        project_finance,
        project_sales,
        project_marketing,
        project_legal,
        project_support,
    ]
    all_entities = relevant_entities + irrelevant_people + irrelevant_projects

    # --- Relations: relevant subgraph (no edges to irrelevant) ---
    rel_alice_bob = Relation(
        name="works_with",
        content="Alice and Bob collaborate on ML infrastructure and API latency for model serving.",
        lhs=alice,
        rhs=bob,
        language=LANG,
        user_id=USER_ID,
    )
    rel_bob_carol = Relation(
        name="reports_to",
        content="Bob reports to Carol for product alignment and prioritization.",
        lhs=bob,
        rhs=carol,
        language=LANG,
        user_id=USER_ID,
    )
    rel_alice_dave = Relation(
        name="manages",
        content="Alice manages Dave on the ML team; they work on training and experiments.",
        lhs=alice,
        rhs=dave,
        language=LANG,
        user_id=USER_ID,
    )
    rel_eve_bob = Relation(
        name="works_with",
        content="Eve and Bob work together on search backend and API contracts.",
        lhs=eve,
        rhs=bob,
        language=LANG,
        user_id=USER_ID,
    )
    rel_eve_carol = Relation(
        name="reports_to",
        content="Eve reports to Carol for Search product direction.",
        lhs=eve,
        rhs=carol,
        language=LANG,
        user_id=USER_ID,
    )
    rel_alice_proj_ml = Relation(
        name="leads",
        content="Alice leads the ML Platform project.",
        lhs=alice,
        rhs=project_ml_platform,
        language=LANG,
        user_id=USER_ID,
    )
    rel_eve_proj_search = Relation(
        name="leads",
        content="Eve leads the Search and Discovery project.",
        lhs=eve,
        rhs=project_search,
        language=LANG,
        user_id=USER_ID,
    )
    rel_dave_proj_ml = Relation(
        name="works_on",
        content="Dave works on ML Platform: recommendation models and experiments.",
        lhs=dave,
        rhs=project_ml_platform,
        language=LANG,
        user_id=USER_ID,
    )
    rel_bob_proj_search = Relation(
        name="works_on",
        content="Bob works on Search backend and API reliability.",
        lhs=bob,
        rhs=project_search,
        language=LANG,
        user_id=USER_ID,
    )
    relevant_relations = [
        rel_alice_bob,
        rel_bob_carol,
        rel_alice_dave,
        rel_eve_bob,
        rel_eve_carol,
        rel_alice_proj_ml,
        rel_eve_proj_search,
        rel_dave_proj_ml,
        rel_bob_proj_search,
    ]

    # --- Relations: irrelevant subgraph only (among irrelevant people/projects) ---
    rel_frank_olivia = Relation(
        name="manages",
        content="Frank manages Olivia in HR team.",
        lhs=frank,
        rhs=olivia,
        language=LANG,
        user_id=USER_ID,
    )
    rel_grace_quinn = Relation(
        name="manages",
        content="Grace manages Quinn in Finance.",
        lhs=grace,
        rhs=quinn,
        language=LANG,
        user_id=USER_ID,
    )
    rel_henry_paul = Relation(
        name="manages",
        content="Henry manages Paul in Sales.",
        lhs=henry,
        rhs=paul,
        language=LANG,
        user_id=USER_ID,
    )
    rel_irene_rachel = Relation(
        name="manages",
        content="Irene manages Rachel in Marketing.",
        lhs=irene,
        rhs=rachel,
        language=LANG,
        user_id=USER_ID,
    )
    rel_jack_sam = Relation(
        name="works_with",
        content="Jack and Sam collaborate on legal and compliance.",
        lhs=jack,
        rhs=sam,
        language=LANG,
        user_id=USER_ID,
    )
    rel_kate_tina = Relation(
        name="manages",
        content="Kate manages Tina in Support.",
        lhs=kate,
        rhs=tina,
        language=LANG,
        user_id=USER_ID,
    )
    rel_leo_maria = Relation(
        name="works_with",
        content="Leo and Maria work on release and test infrastructure.",
        lhs=leo,
        rhs=maria,
        language=LANG,
        user_id=USER_ID,
    )
    rel_frank_proj_hr = Relation(
        name="leads",
        content="Frank leads the HR Portal project.",
        lhs=frank,
        rhs=project_hr,
        language=LANG,
        user_id=USER_ID,
    )
    rel_grace_proj_finance = Relation(
        name="leads",
        content="Grace leads the Finance Dashboard project.",
        lhs=grace,
        rhs=project_finance,
        language=LANG,
        user_id=USER_ID,
    )
    rel_henry_proj_sales = Relation(
        name="leads",
        content="Henry leads the Sales CRM project.",
        lhs=henry,
        rhs=project_sales,
        language=LANG,
        user_id=USER_ID,
    )
    rel_irene_proj_marketing = Relation(
        name="leads",
        content="Irene leads the Marketing Campaigns project.",
        lhs=irene,
        rhs=project_marketing,
        language=LANG,
        user_id=USER_ID,
    )
    rel_jack_proj_legal = Relation(
        name="leads",
        content="Jack leads the Legal Repository project.",
        lhs=jack,
        rhs=project_legal,
        language=LANG,
        user_id=USER_ID,
    )
    rel_kate_proj_support = Relation(
        name="leads",
        content="Kate leads the Support Tooling project.",
        lhs=kate,
        rhs=project_support,
        language=LANG,
        user_id=USER_ID,
    )
    rel_nick_proj_hr = Relation(
        name="works_on",
        content="Nick works on HR Portal UI/UX.",
        lhs=nick,
        rhs=project_hr,
        language=LANG,
        user_id=USER_ID,
    )
    rel_olivia_proj_hr = Relation(
        name="works_on",
        content="Olivia works on HR Portal policies.",
        lhs=olivia,
        rhs=project_hr,
        language=LANG,
        user_id=USER_ID,
    )
    rel_paul_proj_sales = Relation(
        name="works_on",
        content="Paul works on Sales CRM pipeline.",
        lhs=paul,
        rhs=project_sales,
        language=LANG,
        user_id=USER_ID,
    )
    rel_quinn_proj_finance = Relation(
        name="works_on",
        content="Quinn works on Finance Dashboard reporting.",
        lhs=quinn,
        rhs=project_finance,
        language=LANG,
        user_id=USER_ID,
    )
    rel_rachel_proj_marketing = Relation(
        name="works_on",
        content="Rachel works on Marketing campaigns.",
        lhs=rachel,
        rhs=project_marketing,
        language=LANG,
        user_id=USER_ID,
    )
    rel_sam_proj_legal = Relation(
        name="works_on",
        content="Sam works on Legal Repository compliance.",
        lhs=sam,
        rhs=project_legal,
        language=LANG,
        user_id=USER_ID,
    )
    rel_tina_proj_support = Relation(
        name="works_on",
        content="Tina works on Support Tooling knowledge base.",
        lhs=tina,
        rhs=project_support,
        language=LANG,
        user_id=USER_ID,
    )
    rel_maria_proj_support = Relation(
        name="works_on",
        content="Maria works on Support Tooling test automation.",
        lhs=maria,
        rhs=project_support,
        language=LANG,
        user_id=USER_ID,
    )
    rel_leo_proj_support = Relation(
        name="works_on",
        content="Leo works on Support Tooling infrastructure.",
        lhs=leo,
        rhs=project_support,
        language=LANG,
        user_id=USER_ID,
    )
    irrelevant_relations = [
        rel_frank_olivia,
        rel_grace_quinn,
        rel_henry_paul,
        rel_irene_rachel,
        rel_jack_sam,
        rel_kate_tina,
        rel_leo_maria,
        rel_frank_proj_hr,
        rel_grace_proj_finance,
        rel_henry_proj_sales,
        rel_irene_proj_marketing,
        rel_jack_proj_legal,
        rel_kate_proj_support,
        rel_nick_proj_hr,
        rel_olivia_proj_hr,
        rel_paul_proj_sales,
        rel_quinn_proj_finance,
        rel_rachel_proj_marketing,
        rel_sam_proj_legal,
        rel_tina_proj_support,
        rel_maria_proj_support,
        rel_leo_proj_support,
    ]
    all_relations = relevant_relations + irrelevant_relations

    # Populate entity.relations for all relations
    for r in all_relations:
        r.update_connected_entities()

    # --- Episodes: relevant (ML/Search) ---
    episode_weekly = Episode(
        content=(
            "Weekly sync: Alice presented ML platform metrics and training throughput. "
            "Bob discussed search API latency and DB reliability. Carol summarized user feedback and roadmap."
        ),
        language=LANG,
        entities=[alice.uuid, bob.uuid, carol.uuid],
        user_id=USER_ID,
    )
    episode_ml_review = Episode(
        content=(
            "ML review: Alice and Dave walked through recommendation model A/B results. "
            "Eve joined to align on search ranking and feature store usage."
        ),
        language=LANG,
        entities=[alice.uuid, dave.uuid, eve.uuid],
        user_id=USER_ID,
    )
    episode_search_planning = Episode(
        content=(
            "Search planning: Eve, Bob and Carol aligned on Q2 priorities: "
            "unified search API, relevance metrics, and backend SLOs."
        ),
        language=LANG,
        entities=[eve.uuid, bob.uuid, carol.uuid],
        user_id=USER_ID,
    )
    episode_all_hands = Episode(
        content=(
            "All-hands: Carol presented company goals. Alice and Eve gave updates on ML Platform and Search. "
            "Dave and Bob shared engineering wins on experiments and API stability."
        ),
        language=LANG,
        entities=[carol.uuid, alice.uuid, eve.uuid, dave.uuid, bob.uuid],
        user_id=USER_ID,
    )

    # --- Episodes: irrelevant (HR, Finance, Sales, Marketing, etc.) ---
    episode_hr_sync = Episode(
        content=(
            "HR sync: Frank and Olivia reviewed open roles and onboarding checklist. "
            "Discussion on benefits enrollment and policy updates."
        ),
        language=LANG,
        entities=[frank.uuid, olivia.uuid],
        user_id=USER_ID,
    )
    episode_finance_review = Episode(
        content=(
            "Finance review: Grace and Quinn went through Q2 budget and vendor contracts. "
            "P&L and forecasting alignment."
        ),
        language=LANG,
        entities=[grace.uuid, quinn.uuid],
        user_id=USER_ID,
    )
    episode_sales_pipeline = Episode(
        content=(
            "Sales pipeline: Henry and Paul reviewed SMB pipeline and enterprise deals. "
            "Contract renewals and outbound targets."
        ),
        language=LANG,
        entities=[henry.uuid, paul.uuid],
        user_id=USER_ID,
    )
    episode_marketing_standup = Episode(
        content=(
            "Marketing standup: Irene and Rachel discussed campaign calendar and content. Lead gen and social metrics."
        ),
        language=LANG,
        entities=[irene.uuid, rachel.uuid],
        user_id=USER_ID,
    )
    episode_legal_review = Episode(
        content=(
            "Legal review: Jack and Sam went through contract templates and GDPR checklist. IP and compliance updates."
        ),
        language=LANG,
        entities=[jack.uuid, sam.uuid],
        user_id=USER_ID,
    )
    episode_support_sla = Episode(
        content=(
            "Support SLA review: Kate and Tina reviewed ticket volume and escalation rates. "
            "Knowledge base updates and tier-2 training."
        ),
        language=LANG,
        entities=[kate.uuid, tina.uuid],
        user_id=USER_ID,
    )
    episode_devops_release = Episode(
        content=(
            "DevOps release: Leo and Maria aligned on release process and test gates. CI/CD and environment parity."
        ),
        language=LANG,
        entities=[leo.uuid, maria.uuid],
        user_id=USER_ID,
    )
    episode_design_review = Episode(
        content=(
            "Design review: Nick presented mockups for HR Portal and Finance Dashboard. "
            "UI consistency and accessibility."
        ),
        language=LANG,
        entities=[nick.uuid, frank.uuid, grace.uuid],
        user_id=USER_ID,
    )
    episode_company_hr = Episode(
        content=(
            "Company-wide: HR presented new benefits. Finance gave budget overview. "
            "Sales and Marketing shared quarterly results. No engineering or ML content."
        ),
        language=LANG,
        entities=[frank.uuid, grace.uuid, henry.uuid, irene.uuid],
        user_id=USER_ID,
    )

    relevant_episodes = [episode_weekly, episode_ml_review, episode_search_planning, episode_all_hands]
    irrelevant_episodes = [
        episode_hr_sync,
        episode_finance_review,
        episode_sales_pipeline,
        episode_marketing_standup,
        episode_legal_review,
        episode_support_sla,
        episode_devops_release,
        episode_design_review,
        episode_company_hr,
    ]
    all_episodes = relevant_episodes + irrelevant_episodes

    return all_entities, all_relations, all_episodes
