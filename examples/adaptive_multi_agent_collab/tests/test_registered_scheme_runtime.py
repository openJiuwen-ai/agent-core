"""Registered non-judge extensions use the production role/revision protocol."""
import asyncio
from dataclasses import replace
import pytest
from examples.adaptive_multi_agent_collab.providers.adapter import DeterministicFakeAgent
from examples.adaptive_multi_agent_collab.config import SchemeRegistration
from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders
from examples.adaptive_multi_agent_collab.models.policies import WeightingModel
from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection, PromptCollection, protocol_instructions
from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime, ScriptedRoutingPolicy
from examples.adaptive_multi_agent_collab.schemas import Scheme
from examples.adaptive_multi_agent_collab.tests.test_runtime import _offline_config,_example,_matrix
from examples.adaptive_multi_agent_collab.config import TerminationConfig

@pytest.mark.parametrize('edge',[(0,1),(0,0)])
def test_registered_scheme_executes_tagged_role_revision_and_exact_prompt_restore(edge):
    contract=SchemeRegistration('S_check', (edge,), 'Review this supplied pair.',
                                'Find a supported correction if any.', 'Return the best supported answer.',
                                'Audit a supported material issue using the supplied evidence.')
    config=_offline_config(n_agents=2,scheme_registry=(contract,),termination=TerminationConfig(min_sessions=1,max_sessions=1))
    enc=FixedBaseEncoders(2,configuration=config.encoders,scheme_order=config.scheme_order)
    label=Scheme('S_check')
    agents={i:DeterministicFakeAgent(i, {'role':[{'status':'continue','feedback':'Check the stated premise'}, {'status':'complete','feedback':'Addressed'}],
                                      'revision':{'answer':'B','explanation':'corrected'}},default_answer='A') for i in range(2)}
    prompts=seed_prompt_collection(config.scheme_registry)
    assert PromptCollection.from_dict(prompts.to_dict(),config.scheme_registry)==prompts
    runtime=Runtime(config,agents,ScriptedRoutingPolicy(2,{1:_matrix(2,{edge:label})}),
                    WeightingModel(2,enc.history_dimension,configuration=config.network),encoders=enc,prompts=prompts)
    trajectory=asyncio.run(runtime.run(_example()))
    assert trajectory.completed
    assert trajectory.execution_counters["scheme_conversations"]["S_check"] == 1
    trace=trajectory.sessions[0].traces[0]
    assert trace.tag.scheme==label and trace.terminal.answer=='B'
    assert [o.feedback for o in trace.outputs if hasattr(o,'feedback')]==['Check the stated premise','Addressed']
    assert 'exactly "status"' in protocol_instructions('role:S_check',{'admissible_answers':('A','B')})
    assert '"answer" first' in protocol_instructions('revision:S_check',{'admissible_answers':('A','B')})


def test_unsupported_scheme_semantics_and_registry_reassignment_are_rejected():
    args=dict(label='S_check',legal_edges=((0,1),),init_prompt='init',role_prompt='role',revision_prompt='rev',purpose='check')
    with pytest.raises(ValueError,match='unsupported'):
        SchemeRegistration(**args, execution='multi_judge')
    with pytest.raises(ValueError,match='redefine'):
        SchemeRegistration(**{**args,'label':'S_D'})
