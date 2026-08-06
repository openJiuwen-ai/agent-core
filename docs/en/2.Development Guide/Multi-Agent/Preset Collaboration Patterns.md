# Preset Collaboration Patterns for Multi-Agent Teams

This chapter introduces the three built-in multi-agent collaboration patterns in the openJiuwen framework: **Hierarchical** and **Handoff**. These preset patterns encapsulate common multi-agent collaboration scenarios and are ready to use out of the box.

## Overview

openJiuwen provides the following preset collaboration patterns:

| Pattern | Applicable Scenario | Communication Method | Characteristics |
|---------|---------------------|----------------------|-----------------|
| **Hierarchical Tools** | Hierarchical task decomposition | Agent as Tool | Registers sub-Agents as tools via `ability_manager` |
| **Hierarchical MessageBus** | Hierarchical task decomposition | P2P MessageBus | P2P communication via message bus, supports parallel invocation |
| **Handoff** | Sequential collaboration, task handoff | Pub-Sub + State Machine | Agents proactively hand off tasks via tools, supports interrupt and resume |

## Hierarchical Pattern

The hierarchical pattern is suitable for **top-down task decomposition scenarios**, where an upper-level Agent breaks down complex tasks into subtasks and delegates them to lower-level Agents for execution.

### Two Implementation Variants

openJiuwen provides two hierarchical pattern implementations:

#### 1. Hierarchical Tools Pattern

**Core Mechanism**: Sub-Agents are registered as tools via `ability_manager`, and the upper-level Agent invokes sub-Agents through tool calls.

**Features**:
- Simple and intuitive, consistent with regular tool calls
- Sub-Agents are exposed to the LLM as tools
- Suitable for scenarios with clear hierarchy and fixed call relationships

**Use Cases**:
- Research team: Director → Literature Researcher / Data Analyst → Statistics Expert
- Software development: Architect → Frontend Developer / Backend Developer → Test Engineer

#### 2. Hierarchical MessageBus Pattern

**Core Mechanism**: Communication via P2P MessageBus. The Supervisor Agent uses `P2PAbilityManager` to manage sub-Agents, supporting parallel invocation.

**Features**:
- Supports parallel invocation of multiple sub-Agents (concurrency controlled via `max_parallel_sub_agents`)
- Stronger decoupling based on message bus
- Suitable for scenarios requiring parallel processing and dynamic scheduling

**Use Cases**:
- Data processing pipeline: Coordinator → multiple data processing nodes (parallel)
- Distributed task scheduling: Scheduler → multiple worker nodes (parallel)

### Core Components

#### HierarchicalTeamConfig

Configuration class that defines the parameters for a hierarchical team:

**Hierarchical Tools Pattern**:
```python
from openjiuwen.core.multi_agent.teams.hierarchical_tools import HierarchicalTeamConfig

config = HierarchicalTeamConfig(
    root_agent=root_agent_card,  # Root Agent (required)
)
```

**Hierarchical MessageBus Pattern**:
```python
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import HierarchicalTeamConfig

config = HierarchicalTeamConfig(
    supervisor_agent=supervisor_card,  # Supervisor Agent (required)
)
```

#### HierarchicalTeam

Team class that encapsulates the hierarchical collaboration logic:

**Hierarchical Tools Pattern**:
```python
from openjiuwen.core.multi_agent.teams.hierarchical_tools import HierarchicalTeam

team = HierarchicalTeam(card=team_card, config=config)

# Register Agents and specify parent-child relationships
team.add_agent(root_card, root_provider)
team.add_agent(child_card, child_provider, parent_agent_id="root_id")
```

**Hierarchical MessageBus Pattern**:
```python
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import HierarchicalTeam

team = HierarchicalTeam(card=team_card, config=config)

# Register all Agents
team.add_agent(supervisor_card, supervisor_provider)
team.add_agent(sub_agent_card, sub_agent_provider)
```

#### SupervisorAgent (MessageBus Pattern Only)

Built-in Supervisor Agent that combines ReActAgent and P2PAbilityManager:

```python
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import SupervisorAgent

supervisor_card, supervisor_provider = SupervisorAgent.create(
    agents=[sub_agent1_card, sub_agent2_card],  # List of sub-Agents
    model_client_config=model_client_config,
    model_request_config=model_request_config,
    agent_card=supervisor_card,
    system_prompt="You are the coordinator, responsible for assigning tasks to sub-Agents",
    max_iterations=5,
    max_parallel_sub_agents=10,  # Maximum parallel invocations
)
```

### Complete Examples

#### Example 1: Hierarchical Tools Pattern - Three-Layer Research Team

**Scenario**: A Research Director coordinates a Literature Researcher and a Data Analyst. The Data Analyst can invoke a Statistics Expert.

**Team Structure**:
```
Research Director
├── Literature Researcher
└── Data Analyst
    └── Statistics Expert
```

**Full Code**: [hierarchical_tools_research_team.py](../../examples/multi_agent/builtin_teams/hierarchical_tools_research_team.py)

**Core Code Snippet**:

```python
from openjiuwen.core.multi_agent.teams.hierarchical_tools import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
)

# 1. Create the team
team_config = HierarchicalTeamConfig(root_agent=research_director_card)
team = HierarchicalTeam(card=team_card, config=team_config)

# 2. Register Agents and specify parent-child relationships
team.add_agent(research_director_card, lambda: research_director)  # Layer 1
team.add_agent(
    literature_researcher_card,
    lambda: literature_researcher,
    parent_agent_id="research_director"  # Layer 2
)
team.add_agent(
    data_analyst_card,
    lambda: data_analyst,
    parent_agent_id="research_director"  # Layer 2
)
team.add_agent(
    statistics_expert_card,
    lambda: statistics_expert,
    parent_agent_id="data_analyst"  # Layer 3
)

# 3. Run the team
result = await team.invoke({
    "query": "Please research the application of AI in medical diagnosis"
})
```

**Execution Flow**:
1. The Research Director receives the task and decides to invoke the Literature Researcher and Data Analyst
2. The Literature Researcher retrieves relevant literature
3. The Data Analyst analyzes data and invokes the Statistics Expert when needed
4. The Research Director aggregates results and returns the final output

#### Example 2: Hierarchical MessageBus Pattern - Three-Layer Research Team

**Scenario**: Same as Example 1, but implemented using the MessageBus pattern.

**Full Code**: [hierarchical_msgbus_research_team.py](../../examples/multi_agent/builtin_teams/hierarchical_msgbus_research_team.py)

**Core Code Snippet**:

```python
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
    SupervisorAgent,
)

# 1. Create Supervisor Agent (Data Analyst)
data_analyst_card, data_analyst_provider = SupervisorAgent.create(
    agents=[statistics_expert_card],  # Manages the Statistics Expert
    model_client_config=model_client_config,
    model_request_config=model_request_config,
    agent_card=data_analyst_card,
    system_prompt="You are the Data Analyst. You can invoke the Statistics Expert.",
    max_parallel_sub_agents=5
)

# 2. Create root Supervisor Agent (Research Director)
research_director_card, research_director_provider = SupervisorAgent.create(
    agents=[literature_researcher_card, data_analyst_card],
    model_client_config=model_client_config,
    model_request_config=model_request_config,
    agent_card=research_director_card,
    system_prompt="You are the Research Director. You can invoke the Literature Researcher and Data Analyst.",
    max_parallel_sub_agents=5
)

# 3. Create the team and register all Agents
team_config = HierarchicalTeamConfig(supervisor_agent=research_director_card)
team = HierarchicalTeam(card=team_card, config=team_config)

team.add_agent(research_director_card, research_director_provider)
team.add_agent(literature_researcher_card, lambda: literature_researcher)
team.add_agent(data_analyst_card, data_analyst_provider)
team.add_agent(statistics_expert_card, lambda: statistics_expert)

# 4. Run the team
result = await team.invoke({
    "query": "Please research the application of AI in medical diagnosis"
})
```

**Execution Flow**: Same as Example 1, but implemented via MessageBus with support for parallel invocations.

### Comparison of the Two Patterns

| Feature | Hierarchical Tools | Hierarchical MessageBus |
|---------|-------------------|------------------------|
| **Implementation** | ability_manager | P2P MessageBus |
| **Parallel Invocation** | Not supported | Supported (max_parallel_sub_agents) |
| **Configuration Complexity** | Simple | Moderate |
| **Decoupling** | Moderate | High |
| **Applicable Scenario** | Clear hierarchy, sequential calls | Parallel processing, dynamic scheduling |

**Selection Guide**:
- Simple hierarchical tasks → Hierarchical Tools
- Need to invoke multiple sub-Agents in parallel → Hierarchical MessageBus
- Need dynamic scheduling and high decoupling → Hierarchical MessageBus

## Handoff Pattern

The handoff pattern is suitable for **sequential collaboration scenarios**, where Agents collaborate by proactively handing off tasks. Each Agent can decide whether to complete the task or hand it off to another Agent.

### Core Mechanism

- **Tool-Driven**: Each Agent automatically receives injected `transfer_to_{target}` tools
- **State Machine Management**: `HandoffOrchestrator` manages handoff states and routing rules
- **Interrupt and Resume**: Supports task interruption and resumption (via Session persistence)

### Core Components

#### HandoffConfig

Configures handoff rules:

```python
from openjiuwen.core.multi_agent.teams.handoff import HandoffConfig, HandoffRoute

config = HandoffConfig(
    start_agent=triage_card,  # Starting Agent
    max_handoffs=10,  # Maximum number of handoffs
    routes=[  # Handoff routing rules (empty list means fully connected)
        HandoffRoute(source="agent_a", target="agent_b"),
        HandoffRoute(source="agent_b", target="agent_c"),
    ],
    termination_condition=None,  # Optional termination condition function
)
```

#### HandoffTeam

The handoff team class:

```python
from openjiuwen.core.multi_agent.teams.handoff import HandoffTeam, HandoffTeamConfig

team_config = HandoffTeamConfig(handoff=config)
team = HandoffTeam(card=team_card, config=team_config)

# Register Agents
team.add_agent(agent_a_card, agent_a_provider)
team.add_agent(agent_b_card, agent_b_provider)

# Run
result = await team.invoke({"query": "User question"})
```

#### HandoffTool

The automatically injected handoff tool. Agents hand off tasks by calling this tool:

```python
# The Agent's LLM sees the following tools:
# - transfer_to_agent_b: Hand off task to agent_b
# - transfer_to_agent_c: Hand off task to agent_c

# LLM invocation example:
{
    "name": "transfer_to_agent_b",
    "arguments": {
        "reason": "This is a technical issue that needs technical support",
        "message": "User reports login failure"
    }
}
```

### Complete Example

#### Example 3: Handoff Pattern - Customer Service System

**Scenario**: A customer service triage system that routes user inquiries to Technical Support or Billing Support based on the issue type.

**Agent Flow**:
```
Triage Agent
├→ Technical Support
└→ Billing Support
   ↔ (Technical Support and Billing Support can hand off to each other)
```

**Full Code**: [handoff_customer_service.py](../../examples/multi_agent/builtin_teams/handoff_customer_service.py)

**Core Code Snippet**:

```python
from openjiuwen.core.multi_agent.teams.handoff import (
    HandoffTeam,
    HandoffTeamConfig,
    HandoffConfig,
    HandoffRoute,
)

# 1. Configure handoff routing rules
handoff_config = HandoffConfig(
    start_agent=triage_card,  # Start with the Triage Agent
    max_handoffs=5,
    routes=[
        # Triage Agent can hand off to Technical Support or Billing Support
        HandoffRoute(source="triage_agent", target="technical_support"),
        HandoffRoute(source="triage_agent", target="billing_support"),
        # Technical Support and Billing Support can hand off to each other
        HandoffRoute(source="technical_support", target="billing_support"),
        HandoffRoute(source="billing_support", target="technical_support"),
    ]
)

# 2. Create the team
team_config = HandoffTeamConfig(handoff=handoff_config)
team = HandoffTeam(card=team_card, config=team_config)

# 3. Register Agents
team.add_agent(triage_card, lambda: triage_agent)
team.add_agent(technical_support_card, lambda: technical_support)
team.add_agent(billing_support_card, lambda: billing_support)

# 4. Run
result = await team.invoke({"query": "Why is my bill so expensive?"})
```

**Execution Flow**:
1. The Triage Agent analyzes the issue type
2. Identifies it as a billing issue and calls `transfer_to_billing_support`
3. The Billing Support Agent processes the billing issue and returns the result

**Agent System Prompt Examples**:

```python
# Triage Agent
"You are a customer service triage specialist. Analyze the user's issue:\n"
"- Technical issues (product usage, malfunctions, features) → Hand off to technical_support\n"
"- Billing issues (payments, refunds, invoices) → Hand off to billing_support\n"
"- Simple greetings or thanks → Reply directly\n"
"Use the transfer_to_xxx tool to hand off tasks."

# Technical Support Agent
"You are a technical support specialist responsible for resolving product technical issues."
"Provide detailed troubleshooting steps and solutions."
"If the issue is beyond technical scope (e.g., billing), hand off to billing_support."

# Billing Support Agent
"You are a billing support specialist responsible for handling billing, payment, and refund issues."
"Provide clear billing explanations and payment guidance."
"If the issue is technical, hand off to technical_support."
```

### Advanced Features

#### 1. Interrupt and Resume

The Handoff pattern supports task interruption and resumption:

```python
# Agent returns an interrupt signal
return {"result_type": "interrupt", "message": "User confirmation required"}

# Or raise an AgentInterrupt exception
from openjiuwen.core.session.interaction.base import AgentInterrupt
raise AgentInterrupt("User input required")

# Resume execution
result = await team.invoke({"query": "Continue"}, session=previous_session)
```

#### 2. Custom Termination Condition

```python
def custom_termination(orchestrator):
    # Terminate when handoff count exceeds 3
    return orchestrator.handoff_count > 3

config = HandoffConfig(
    start_agent=start_card,
    termination_condition=custom_termination
)
```

#### 3. Fully Connected Routing

When `routes` is an empty list, any Agent can hand off to any other Agent:

```python
config = HandoffConfig(
    start_agent=start_card,
    routes=[],  # Fully connected
)
```

## Best Practices

### 1. Choose the Right Collaboration Pattern

| Scenario | Recommended Pattern |
|----------|---------------------|
| Top-down task decomposition with clear hierarchy | Hierarchical Tools |
| Need to invoke multiple sub-Agents in parallel | Hierarchical MessageBus |
| Sequential collaboration between Agents with flexible handoff | Handoff |
| Need interrupt/resume and state persistence | Handoff |

### 2. Design Clear Agent Responsibilities

Each Agent should have well-defined responsibility boundaries:

```python
# Good design
"You are a technical support specialist responsible for resolving product technical issues"

# Poor design
"You are a customer service specialist responsible for handling all user issues"
```

### 3. Configure Handoff Rules Properly

- **Hierarchical Pattern**: Define clear parent-child relationships; avoid circular dependencies
- **Handoff Pattern**: Set a reasonable `max_handoffs` to prevent infinite loops

### 4. Write High-Quality System Prompts

System prompts should clearly specify:
- The Agent's scope of responsibility
- Available sub-Agents / handoff targets
- When to invoke / hand off

### 5. Handle Exception Cases

```python
try:
    result = await team.invoke(inputs)
except Exception as e:
    # Handle team execution exceptions
    logger.error(f"Team execution failed: {e}")
```

## FAQ

### Q1: What is the difference between Hierarchical Tools and MessageBus patterns?

**A**: The main differences lie in communication method and parallelism:
- **Tools Pattern**: Registers sub-Agents as tools via `ability_manager`; sequential invocation
- **MessageBus Pattern**: Communicates via P2P message bus; supports parallel invocation (max_parallel_sub_agents)

### Q2: How does the Handoff pattern prevent infinite loops?

**A**: Through the following mechanisms:
1. `max_handoffs` limits the maximum number of handoffs
2. `termination_condition` provides custom termination criteria
3. `routes` restricts handoff routing to avoid circular paths

### Q3: How do I debug multi-agent collaboration?

**A**:
1. Enable logging: `logger.setLevel(logging.DEBUG)`
2. Inspect `handoff_history` (Handoff pattern)
3. Use the `stream()` method to view intermediate results in real time

### Q4: Can I mix multiple patterns?

**A**: Yes. For example:
- A sub-Agent in a Hierarchical team can itself be a Handoff team
- An Agent in a Handoff team can be a Hierarchical team

## Related Documentation

- [Multi-Agent Overview](./Overview.md)
- [TeamRuntime and CommunicableAgent](./TeamRuntime-and-CommunicableAgent.md)
- [BaseTeam](./BaseTeam.md)
- [Agent as Tool](./AgentAsTool.md)
