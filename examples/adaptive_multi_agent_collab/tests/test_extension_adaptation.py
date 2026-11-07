"""Incremental scopes preserve learned parent values and nonzero Adam state."""
from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from examples.adaptive_multi_agent_collab.config import Config, PhaseConfig, PromptPhaseConfig, SelectionConfig
from examples.adaptive_multi_agent_collab.models.encoders import offline_encoder_configuration
from examples.adaptive_multi_agent_collab.learning.extension import (
    ExtensionPlan, RestrictedAdaptation, _tensor_equal, check_parent_retention, named_optimizer_state,
)
from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
from examples.adaptive_multi_agent_collab.learning.training import (
    InterleavedTrainer, PromptOptimizer, WeightingDiagnostics, update_weighting, update_routing,
)
from examples.adaptive_multi_agent_collab.schemas import ConversationTag, Scheme


class Parent(nn.Module):
    def __init__(self):
        super().__init__()
        self.inherited = nn.Parameter(torch.tensor([.4, -.3]))


class NoProposals:
    def metadata(self):
        return {"offline": True}


def plan(config, **changes):
    values = dict(parent_condition='parent', extension_condition='extension', train_ids=('t',),
                  component_validation_ids=('c',), system_validation_ids=('s',),
                  weighting_phase=PhaseConfig(max_updates=1, max_attempts=1, validation_interval=1),
                  routing_phase=PhaseConfig(max_updates=1, max_attempts=1, validation_interval=1),
                  spending_limit_usd=.1, physical_attempt_limit=10, validation_attempt_limit=10, calibration_attempt_limit=1,
                  calibration_candidates=(config.termination,), acceptance_min_improvement=0.,
                  retained_accuracy_tolerance=0., parent_value_tolerance=0., min_scheme_coverage={'debate': 1},
                  min_agent_incoming_coverage={}, min_agent_outgoing_coverage={}, min_free_zero_coverage=0,
                  prompt_components=(), prompt_phase=PromptPhaseConfig(max_iterations=0), escalation_scopes=())
    values.update(changes)
    return ExtensionPlan(**values)


def build():
    parents = [Parent(), Parent()]
    parent_optimizers = [torch.optim.Adam(model.parameters(), lr=.01) for model in parents]
    for model, optimizer in zip(parents, parent_optimizers):
        model.inherited.square().sum().backward()
        optimizer.step()
    models = [copy.deepcopy(model) for model in parents]
    for model in models:
        model.register_parameter('added', nn.Parameter(torch.zeros(2)))
    expansion = SimpleNamespace(alpha=models[0], weighting=models[1], alpha_parameters=('added',),
                                weighting_parameters=('added',), metadata={'boundary': 1})
    config = Config(n_agents=2, encoders=offline_encoder_configuration(), selection=SelectionConfig(.002))
    trainer = InterleavedTrainer(alpha=models[0], weighting=models[1], gamma=seed_prompt_collection(),
        alpha_optimizer=torch.optim.Adam(models[0].parameters()), weighting_optimizer=torch.optim.Adam(models[1].parameters()),
        prompt_optimizer=PromptOptimizer(NoProposals()), config=config)
    controller = RestrictedAdaptation(trainer, expansion, plan(config), parent_alpha=parents[0], parent_weighting=parents[1],
                                     parent_alpha_optimizer=parent_optimizers[0], parent_weighting_optimizer=parent_optimizers[1])
    return controller


def test_learned_parent_adam_counters_moments_and_values_untouched():
    controller = build()
    before = copy.deepcopy(controller.inherited)
    assert before['alpha']['state']['inherited']['step'] == 1
    assert before['alpha']['state']['inherited']['exp_avg'].abs().sum() > 0
    trainer = controller.trainer
    logits = trainer.weighting.inherited + trainer.weighting.added
    session = SimpleNamespace(resolved_answers=('yes','no'), weights=logits.softmax(0), weight_logits=logits)
    assert update_weighting(trainer.weighting, trainer.weighting_optimizer, [session], 'yes') is not None
    tag = ConversationTag(1,0,1,Scheme.DEBATE)
    prediction = (trainer.alpha.inherited + trainer.alpha.added).sum()
    action = SimpleNamespace(decisions=[SimpleNamespace(edge=(0,1),tag=tag,scheme=Scheme.DEBATE,q_value=prediction,free=True)])
    assert update_routing(trainer.alpha_optimizer,[action],[{tag:.3}],[0.],local_labels=[{tag:1}],
                          zero_labels=[{0:1}],conversation_costs=[{tag:.2}],p_mix=.75,lambda_conversation=.002) is not None
    controller.assert_frozen()
    assert _tensor_equal(controller.inherited, before)
    assert trainer.alpha_optimizer.state[trainer.alpha.added]['step'] == 1
    assert trainer.weighting_optimizer.state[trainer.weighting.added]['step'] == 1
    assert trainer.alpha.inherited not in trainer.alpha_optimizer.state


def test_disconnected_permitted_blocks_skip_optimizer():
    controller = build()
    # No permitted parameter participates: no Adam counters/momentum may advance.
    logits = controller.trainer.weighting.inherited
    session = SimpleNamespace(resolved_answers=('yes','no'),weights=logits.softmax(0),weight_logits=logits)
    diagnostics = WeightingDiagnostics()
    assert update_weighting(controller.trainer.weighting,controller.trainer.weighting_optimizer,[session],'yes',
                            diagnostics=diagnostics) is None
    assert not controller.trainer.weighting_optimizer.state
    assert diagnostics.informative_sessions == diagnostics.skipped_w_updates == 1
    assert diagnostics.successful_w_updates == diagnostics.failed_w_updates == 0
    controller.assert_frozen()


def test_extension_plan_requires_every_budget_and_partition_isolation():
    controller = build()
    encoded = controller.plan.to_dict()
    assert ExtensionPlan.from_dict(encoded).fingerprint == controller.plan.fingerprint
    del encoded['retained_accuracy_tolerance']
    with pytest.raises(TypeError):
        ExtensionPlan.from_dict(encoded)
    with pytest.raises(ValueError,match='disjoint'):
        replace(controller.plan, component_validation_ids=('t',))


def test_checkpoint_plan_mismatch_rejected_without_mutation():
    controller = build()
    value = controller.state_dict()
    value['plan_fingerprint'] = 'changed'
    before = copy.deepcopy(controller.progress)
    with pytest.raises(ValueError,match='different scientific plan'):
        controller.load_state_dict(value)
    assert controller.progress == before


def test_parent_mode_retention_is_not_enlarged_validation():
    result = check_parent_retention([(torch.tensor([.4]),torch.tensor([.2]))],
                                    [(torch.tensor([.4]),torch.tensor([.2]))],tolerance=0.)
    assert result['passed'] and result['mode']=='parent_only'
    assert 'accuracy' not in result
    assert not check_parent_retention([(torch.tensor([.4]),torch.tensor([.2]))],
                                     [(torch.tensor([.5]),torch.tensor([.2]))],tolerance=.01)['passed']


def test_declared_escalation_requires_approval_and_keeps_inherited_moments():
    from dataclasses import asdict
    from examples.adaptive_multi_agent_collab.learning.training import _fingerprint
    controller = build()
    phase = asdict(controller.plan.routing_phase)
    scope = {'name':'declared_heads','alpha_parameters':['inherited','added'],
             'weighting_parameters':['inherited','added'],'weighting_phase':phase,'routing_phase':phase,
             'spending_limit_usd':.1,'physical_attempt_limit':10,'validation_attempt_limit':10,'calibration_attempt_limit':1}
    controller.plan = replace(controller.plan,escalation_scopes=(scope,))
    controller.progress.phase='complete'
    with pytest.raises(ValueError,match='approval'):
        controller.select_escalation('declared_heads',{})
    approval={'decision':'approve','plan_fingerprint':controller.plan.fingerprint,
              'scope_fingerprint':_fingerprint(scope),'scope_name':'declared_heads','approval_source':'offline fixture'}
    controller.select_escalation('declared_heads',approval)
    inherited = controller.trainer.alpha_optimizer.state[controller.trainer.alpha.inherited]
    assert torch.equal(inherited['exp_avg'],controller.inherited['alpha']['state']['inherited']['exp_avg'])
    assert inherited['step']==1
    with pytest.raises(ValueError,match='completed adaptation boundary'):
        controller.select_escalation('declared_heads',approval)


def test_development_scope_is_atomic_cumulative_and_nonresetting():
    from examples.adaptive_multi_agent_collab.config import ResourceConfig
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger, BudgetExhausted
    limits=ResourceConfig(development_budget=.5,currency='USD')
    ledger=ExpenditureLedger(limits)
    ledger.stage='development'
    bindings={'protocol':'append_blocks_restricted_fresh_trajectory_v1','condition_fingerprint':'c',
              'plan_fingerprint':'p','authorization_fingerprint':'a'}
    ledger.register_scope('x',stage='development',max_attempts=2,budget=.2,bindings=bindings)
    ledger.activate_scope('x')
    ledger.reserve(.15,kind='role',model='fake',metadata={})
    with pytest.raises(BudgetExhausted):
        ledger.reserve(.06,kind='role',model='fake',metadata={})
    with pytest.raises(ValueError,match='cannot change'):
        ledger.register_scope('x',stage='development',max_attempts=3,budget=.3,bindings=bindings)
    restored=ExpenditureLedger(limits)
    restored.load_state_dict(ledger.state_dict())
    with pytest.raises(BudgetExhausted):
        restored.reserve(.06,kind='role',model='fake',metadata={})
    assert restored.scope_summary('x')['unresolved']==.15


def test_coverage_requires_an_explicit_threshold_for_each_addition():
    controller = build()
    with pytest.raises(ValueError, match='every new'):
        controller.validate_coverage_registry(parent_agents=(0,1),agents=(0,1,2),
            parent_schemes=('debate',),schemes=('debate','new'))
    controller.plan = replace(controller.plan,min_scheme_coverage={'new':0},
        min_agent_incoming_coverage={2:0},min_agent_outgoing_coverage={2:0})
    controller.validate_coverage_registry(parent_agents=(0,1),agents=(0,1,2),
        parent_schemes=('debate',),schemes=('debate','new'))


def test_escalated_checkpoint_rebuilds_scope_entry_and_nonzero_optimizer_state(tmp_path):
    from dataclasses import asdict
    from examples.adaptive_multi_agent_collab.learning.training import _fingerprint
    controller = build()
    original = controller.trainer.config.termination
    selected = replace(original,max_sessions=original.max_sessions+1)
    phase = asdict(controller.plan.routing_phase)
    scope = {'name':'heads','alpha_parameters':['inherited','added'],'weighting_parameters':['inherited','added'],
        'weighting_phase':phase,'routing_phase':phase,'spending_limit_usd':.1,'physical_attempt_limit':10,
        'validation_attempt_limit':10,'calibration_attempt_limit':1}
    declared = replace(controller.plan,calibration_candidates=(original,selected),escalation_scopes=(scope,))
    controller.plan=declared;controller.progress.phase='complete'
    controller.initial_retention={'passed':True,'tolerance':0.}
    controller.trainer.config=replace(controller.trainer.config,termination=selected)
    controller.trainer.state.calibration_complete=True
    approval={'decision':'approve','plan_fingerprint':declared.fingerprint,'scope_fingerprint':_fingerprint(scope),
              'scope_name':'heads','approval_source':'offline explicit scope'}
    controller.select_escalation('heads',approval)
    trainer=controller.trainer
    (trainer.alpha.inherited+trainer.alpha.added).square().sum().backward()
    trainer.alpha_optimizer.step();trainer.alpha_optimizer.zero_grad()
    saved=tmp_path/'scoped.pt';controller.save(saved)
    restored=build();restored.plan=declared
    payload=torch.load(saved,weights_only=False)
    restored.configure_checkpoint_scope(payload['extension'])
    restored.trainer.resume_from_checkpoint(saved)
    assert restored.trainer.declared_config==trainer.declared_config
    assert restored.scope_entry_termination==selected and not restored.trainer.state.calibration_complete
    assert _tensor_equal(named_optimizer_state(restored.trainer.alpha,restored.trainer.alpha_optimizer),
                         named_optimizer_state(trainer.alpha,trainer.alpha_optimizer))
    restored.assert_frozen()


def test_parent_validation_subset_keeps_exact_selected_predictions():
    from examples.adaptive_multi_agent_collab.operations.extension_experiment import _matching_parent_evaluation
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _plan,_record
    record=_record(_plan('sysval'))
    requested=type(record)(replace(record.plan,ordered_example_ids=record.plan.ordered_example_ids[:1]),
                           record.examples[:1],record.objective)
    payload={'best_system':{'evaluation':record.to_dict()}}
    _matching_parent_evaluation(payload,record.plan,requested)
    altered=replace(requested,examples=(replace(requested.examples[0],prediction='different'),))
    with pytest.raises(ValueError,match='exactly'):
        _matching_parent_evaluation(payload,record.plan,altered)


def test_offline_prepare_uses_inert_models_and_publishes_canonical_journal(tmp_path,monkeypatch):
    import json
    from examples.adaptive_multi_agent_collab.operations import extension_experiment as module
    from examples.adaptive_multi_agent_collab.operations.policy_activation import _inert_model
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume
    controller = build()
    ledger = ExpenditureLedger(controller.trainer.config.resources)
    hooks = SimpleNamespace(expenditure_ledger=ledger,state_dict=lambda:{'expenditure_ledger':ledger.state_dict()})
    controller.trainer.hooks = hooks
    campaign = tmp_path/'campaign';campaign.mkdir()
    (campaign/'expenditure_ledger.json').write_text(json.dumps(ledger.state_dict()))
    parent = campaign/'checkpoint.pt';controller.trainer.save_checkpoint(parent)
    registration = {'references':{'parent_checkpoint':str(parent),'extension_manifest':'fixture'},
                    'plan_fingerprint':controller.plan.fingerprint,'plan':controller.plan.to_dict(),
                    'registration_fingerprint':'fixture','parent_condition':'parent','extension_condition':'extension'}
    monkeypatch.setattr(module,'validate_extension_registration',lambda _:registration)
    monkeypatch.setattr(module,'_canonical_campaign',lambda _:campaign)
    seen=[]
    def factory(_,**kwargs):
        seen.append(kwargs['model_factory'])
        assert kwargs['model_factory'] is _inert_model
        return controller,hooks,registration
    monkeypatch.setattr(module,'build_extension_registration',factory)
    monkeypatch.setattr(module,'retention_for_registration',lambda *_:{'passed':True,'tolerance':0.})
    output = campaign/'extensions'/controller.plan.fingerprint
    result = module.prepare_extension('fixture',output)
    assert seen==[_inert_model] and result['prepared'] and not result['activated']
    assert read_journal(campaign)['boundary'] is not None
    validate_journal_resume(output/'checkpoint.pt',campaign)
    assert not ledger.entries
    with pytest.raises(ValueError,match='cannot reset'):
        module.prepare_extension('fixture',output)


def test_restricted_runtime_phases_calibration_and_reload(tmp_path):
    """Real encoders/networks/Runtime, mocked agents, one fresh workflow per step."""
    import asyncio
    from examples.adaptive_multi_agent_collab.providers.adapter import DeterministicFakeAgent
    from examples.adaptive_multi_agent_collab.learning.calibration import TerminationCalibrationPlan, calibrate_termination
    from examples.adaptive_multi_agent_collab.config import NetworkConfig, TerminationConfig
    from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders
    from examples.adaptive_multi_agent_collab.learning.evaluation import EvaluationPlan, EvaluationRecord, ExampleEvaluation, _trajectory_evaluation, scoring_configuration
    from examples.adaptive_multi_agent_collab.models.policies import RoutingPolicy, WeightingModel, expand_models
    from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime
    from examples.adaptive_multi_agent_collab.schemas import QueryExample, RoutingMode, SCHEME_ORDER

    torch.manual_seed(19)
    network=NetworkConfig(representation_dim=16,feedforward_dim=24,answer_byte_dim=8,transformer_layers=1,transformer_heads=4)
    enc=FixedBaseEncoders(2,configuration=offline_encoder_configuration())
    parent_alpha=RoutingPolicy(2,enc.history_dimension,configuration=network)
    parent_w=WeightingModel(2,enc.history_dimension,configuration=network)
    parent_optim=[torch.optim.Adam(parent_alpha.parameters()),torch.optim.Adam(parent_w.parameters())]
    # Nonzero inherited head and optimizer history make restricted gradient paths real.
    for model,opt in zip((parent_alpha,parent_w),parent_optim):
        sum(p.square().sum() for p in model.parameters()).backward();opt.step();opt.zero_grad()
    with torch.no_grad():
        parent_w.reliability_head.output.weight.fill_(.01)
    expanded=expand_models(parent_alpha,parent_w,enc,agent_ids=(0,1,2),scheme_order=SCHEME_ORDER)
    config=Config(n_agents=3,network=network,encoders=offline_encoder_configuration(),selection=SelectionConfig(.002),
                  termination=TerminationConfig(min_sessions=1,max_sessions=2))
    trainer=InterleavedTrainer(alpha=expanded.alpha,weighting=expanded.weighting,gamma=seed_prompt_collection(),
        alpha_optimizer=torch.optim.Adam(expanded.alpha.parameters()),weighting_optimizer=torch.optim.Adam(expanded.weighting.parameters()),
        prompt_optimizer=PromptOptimizer(NoProposals()),config=config)
    from dataclasses import asdict
    from examples.adaptive_multi_agent_collab.learning.training import _fingerprint
    shorter=TerminationConfig(min_sessions=1,max_sessions=1)
    phase=asdict(PhaseConfig(max_updates=1,max_attempts=1,validation_interval=1))
    scope={'name':'reviewed_heads','alpha_parameters':list(expanded.alpha_parameters),
           'weighting_parameters':list(expanded.weighting_parameters),'weighting_phase':phase,'routing_phase':phase,
           'spending_limit_usd':.1,'physical_attempt_limit':10,'validation_attempt_limit':10,'calibration_attempt_limit':1}
    declared=plan(config,min_scheme_coverage={},min_free_zero_coverage=0,
        calibration_candidates=(config.termination,shorter),escalation_scopes=(scope,))
    controller=RestrictedAdaptation(trainer,expanded,declared,parent_alpha=parent_alpha,parent_weighting=parent_w,
        parent_alpha_optimizer=parent_optim[0],parent_weighting_optimizer=parent_optim[1])
    examples={key:QueryExample(key,'Choose a registered synthetic token.',('A','B','C'),'A') for key in ('t','c','s')}
    agents={i:DeterministicFakeAgent(i,{'role':{'status':'complete','feedback':'No material issue.'}},default_answer=chr(65+i)) for i in range(3)}
    class Hooks:
        authorization_required=False
        expenditure_ledger=None
        active_data_plan=SimpleNamespace(train_ids=('t',),component_validation_ids=('c',),system_validation_ids=('s',))
        def __init__(self): self.calls=[];self.sampling={'weighting':0,'routing':0}
        def bind(self,value): self.trainer=value
        def state_dict(self): return {'condition':'synthetic extension','calls':list(self.calls),'sampling':dict(self.sampling)}
        def validate_state(self,value):
            if value['condition']!='synthetic extension': raise ValueError('condition')
        def load_state_dict(self,value): self.calls=list(value['calls']);self.sampling=dict(value['sampling'])
        def runtime(self): return Runtime(self.trainer.config,agents,self.trainer.alpha,self.trainer.weighting,prompts=self.trainer.gamma,encoders=expanded.encoders)
        def evaluation_plan(self,component):
            return EvaluationPlan(('s',) if component=='system' else ('c',),
                {'system':'sysval','routing':'alphaval','weighting':'wval'}[component],42,
                'sys-val' if component=='system' else 'comp-val',scoring_configuration=scoring_configuration(config))
        async def trajectory(self,component,split,*,routing_identity):
            self.calls.append(component);self.sampling[component]+=1
            return await self.runtime().run(examples['t'],routing_mode=RoutingMode.TRAIN,routing_identity=routing_identity,partition='train')
        async def validate(self,component):
            ep=self.evaluation_plan(component)
            with torch.no_grad(): result=await self.runtime().run(examples['c'],routing_mode=RoutingMode.EVALUATION,partition='comp-val')
            return EvaluationRecord(ep,(_trajectory_evaluation(result,seed_replay_supported=False),),'negative_weighting_nll' if component=='weighting' else 'selection')
        async def system_validation(self):
            result=await self.runtime().run(examples['s'],routing_mode=RoutingMode.EVALUATION,partition='sys-val')
            return EvaluationRecord(self.evaluation_plan('system'),(_trajectory_evaluation(result,seed_replay_supported=False),))
        async def calibrate_termination(self):
            ep=self.evaluation_plan('system');outgoing,incoming=config.capacity.resolve(3)
            cp=TerminationCalibrationPlan(ep,declared.calibration_candidates,controller.scope_entry_termination,3,outgoing,incoming,tuple(s.value for s in SCHEME_ORDER))
            async def shared(search):
                observed=await self.runtime().run_shared_prefix(examples['s'],search.candidates,partition='sys-val',generation_context=ep.generation_context('s'))
                records=tuple(EvaluationRecord(ep,(_trajectory_evaluation(t,seed_replay_supported=False),)) for t in observed.prefixes)
                return records,()
            record=await calibrate_termination(cp,shared)
            trainer.apply_configuration(replace(trainer.config,termination=record.selected_configuration))
            return record
    hooks=Hooks()
    parent_record=EvaluationRecord(hooks.evaluation_plan('system'),(ExampleEvaluation('s','B',1,1000.,False,0,0),))
    result=asyncio.run(controller.run(hooks,parent_result=parent_record,parent_retention={'passed':True,'tolerance':0.}))
    assert hooks.calls==['weighting','routing']
    assert trainer.state.updates['routing']==1 and trainer.state.updates['weighting']==1
    assert result['accepted'] and result['calibration'] is not None
    assert trainer.state.phase=='complete' and trainer.best_system is not None
    controller.assert_frozen()
    target=tmp_path/'extension.pt';controller.save(target)
    saved=torch.load(target,weights_only=False)
    assert saved['extension']['plan_fingerprint']==declared.fingerprint
    assert saved['extension']['inherited_optimizers']['alpha']['state']
    controller.validate_state(saved['extension'])
    trainer.resume_from_checkpoint(target,hooks=hooks)
    assert asyncio.run(controller.run(hooks,parent_result=parent_record,parent_retention={'passed':True,'tolerance':0.}))['accepted']
    assert hooks.calls==['weighting','routing']
    assert trainer.config.termination==shorter
    approval={'decision':'approve','plan_fingerprint':declared.fingerprint,'scope_fingerprint':_fingerprint(scope),
              'scope_name':scope['name'],'approval_source':'Explicit offline scope fixture'}
    controller.select_escalation(scope['name'],approval)
    assert not trainer.state.calibration_complete and controller.scope_entry_termination==shorter
    assert trainer.best_system is None
    controller.save(target)
    # Reconstruct the exact scope without changing registered scientific settings.
    from examples.adaptive_multi_agent_collab.learning.extension import scope_configuration
    saved=torch.load(target,weights_only=False)
    restored_config=scope_configuration(config,saved['extension'],declared)
    assert restored_config==trainer.declared_config
    trainer.resume_from_checkpoint(target,hooks=hooks)
    result=asyncio.run(controller.run(hooks,parent_result=parent_record,
        parent_retention={'passed':False,'tolerance':0.,'maximum_absolute_error':.2}))
    assert result['accepted'] and trainer.state.calibration_complete
    assert hooks.calls==['weighting','routing','weighting','routing']
    assert trainer.state.updates['routing']==trainer.state.updates['weighting']==2
    controller.assert_frozen()
    controller.save(target);trainer.resume_from_checkpoint(target,hooks=hooks)
    assert trainer.state.phase=='complete' and trainer.best_system is not None
