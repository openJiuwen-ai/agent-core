import hashlib
import json

from .schemas import MCQExample
PROMPT_VERSION = "reviewer-revision-v3"
ROLE_PROMPTS = {
    0: "You are an analytical solver. Identify relevant facts, compare every option concisely, and select the strongest answer.",
    1: "You are an option eliminator. Inspect every distractor, explain why rejected options are unsupported, and select the strongest remainder.",
    2: "You are a skeptical verifier. Check hidden assumptions and counterexamples, challenge the obvious answer, compare plausible alternatives, and select the best support.",
}
_ANSWER_FORMAT = (
    'Return only a JSON object with keys "answer" and "justification"; "answer" must be exactly '
    'one label from A, B, C, D, or E and "justification" must be a brief explanation.'
)
_REVIEW_FORMAT = (
    'Return only JSON with "status", "feedback", and "recommended_answer". Status must be '
    '"continue" or "complete"; feedback must be specific; recommended_answer must be A, B, C, D, or E. '
    '"complete" accepts the submitted answer, so recommended_answer must match it. '
    '"continue" means recommending reconsideration and may recommend a different label.'
)
PROMPT_HASH = hashlib.sha256(json.dumps([PROMPT_VERSION, ROLE_PROMPTS, _ANSWER_FORMAT, _REVIEW_FORMAT], sort_keys=True).encode()).hexdigest()
def format_question(example: MCQExample) -> str:
    return f"Question: {example.question}\nChoices:\n" + "\n".join(f"{label}. {text}" for label, text in example.options.items())
def initial_prompt(example: MCQExample, agent_id: int) -> str:
    return f"{ROLE_PROMPTS[agent_id]}\n{format_question(example)}\nGive a concise justification, not private chain-of-thought.\n{_ANSWER_FORMAT}"
def reviewer_prompt(
    example: MCQExample, reviewer_id: int, initiator_id: int, answer: str, justification: str, corrective: bool = False,
) -> str:
    correction = "\nFORMAT CORRECTION: complete must retain the submitted answer." if corrective else ""
    return (
        f"{ROLE_PROMPTS[reviewer_id]}\nAct as reviewer for Agent {initiator_id}.\n{format_question(example)}\n"
        f"Submitted answer: {answer}\n"
        f"Submitted justification: {justification}\n{_REVIEW_FORMAT}{correction}"
    )
def revision_prompt(
    example: MCQExample, agent_id: int, current_answer: str, reviewer_feedback: str, recommended_answer: str,
) -> str:
    return (
        f"{ROLE_PROMPTS[agent_id]}\n{format_question(example)}\nYour current answer is {current_answer}. "
        f"Reviewer feedback: {reviewer_feedback}\nThe reviewer recommends {recommended_answer}. "
        f"Reconsider once; you may keep or change your answer.\n{_ANSWER_FORMAT}"
    )
