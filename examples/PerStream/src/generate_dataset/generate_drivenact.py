import argparse
import base64
import csv
import io
import json
import os
import openai
from tenacity import Retrying, RetryError, retry, stop_after_attempt, wait_random
import torchvision
from PIL import Image
from typing import Any, Dict, List
from tqdm import tqdm
from multiprocessing import Pool


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Generate DrivenAct dataset with passive and proactive QA pairs")
    
    # Required paths
    parser.add_argument('--drivenact_path', type=str, required=True,
                       help='Path to DrivenAct dataset root directory')
    parser.add_argument('--drivenact_dataset', type=str, required=True,
                       help='Name of the DrivenAct dataset subdirectory')
    parser.add_argument('--annotations_csv', type=str, required=True,
                       help='Path to annotations CSV file (e.g., midlevel.chunks_90.csv)')
    
    # Optional paths
    parser.add_argument('--cache_dir', type=str, default='.cache_drivenact',
                       help='Directory for caching intermediate results')
    parser.add_argument('--output_dir', type=str, default='.',
                       help='Output directory for generated datasets')
    parser.add_argument('--passive_output_dir', type=str, default=None,
                       help='Output directory for passive dataset (default: {output_dir}/passive_drivenact)')
    parser.add_argument('--proactive_output_dir', type=str, default=None,
                       help='Output directory for proactive dataset (default: {output_dir}/proactive_drivenact)')
    
    # Model and API configuration
    parser.add_argument('--model_id', type=str, default='openai/gpt-4o-mini',
                       help='Model ID for API calls')
    parser.add_argument('--api_key', type=str, default=None,
                       help='OpenAI API key (default: from OPENAI_API_KEY env var)')
    parser.add_argument('--api_base', type=str, default=None,
                       help='API base URL (default: OpenAI standard)')
    
    # Video processing parameters
    parser.add_argument('--scale_down', type=int, default=1,
                       help='Scale down factor for video frames')
    parser.add_argument('--video_start_before', type=float, default=5.0,
                       help='Seconds to extract before timestamp')
    parser.add_argument('--video_end_after', type=float, default=2.0,
                       help='Seconds to extract after timestamp')
    parser.add_argument('--fps', type=float, default=15.0,
                       help='Frames per second for timestamp conversion')
    
    # Processing parameters
    parser.add_argument('--participant_ids', type=str, default=None,
                       help='Comma-separated list of participant IDs (default: auto-detect from CSV)')
    parser.add_argument('--num_workers', type=int, default=8,
                       help='Number of parallel workers')
    parser.add_argument('--vt1t2_correctness_threshold', type=float, default=9.0,
                       help='Minimum correctness threshold for vt1t2 answers')
    parser.add_argument('--v_correctness_threshold', type=float, default=6.0,
                       help='Maximum correctness threshold for v-only answers (to filter)')
    
    return parser.parse_args()


def generate_enc(args_tuple):
    """Generate dataset for a single participant ID."""
    args, pid = args_tuple
    
    # Setup API configuration
    if args.api_key:
        openai.api_key = args.api_key
    else:
        openai.api_key = os.getenv('OPENAI_API_KEY')
        if not openai.api_key:
            raise ValueError("API key must be provided via --api_key or OPENAI_API_KEY environment variable")
    
    if args.api_base:
        openai.api_base = args.api_base
    
    # Setup paths
    cache_dir = os.path.join(args.cache_dir, str(pid))
    passive_output_dir = args.passive_output_dir or os.path.join(args.output_dir, 'passive_drivenact')
    proactive_output_dir = args.proactive_output_dir or os.path.join(args.output_dir, 'proactive_drivenact')
    video_base_path = os.path.join(args.drivenact_path, args.drivenact_dataset)
    
    def get_video_path(video_uid: str) -> str:
        """Get full path to video file."""
        return os.path.join(video_base_path, f"{video_uid}.mp4")
    
    def get_cache_path(cache_name: str) -> str:
        """Get path to cache file."""
        return os.path.join(cache_dir, cache_name)
    
    def filter_video_uids(participant_id: int) -> List[int]:
        """Filter video UIDs for a specific participant."""
        metadata_path = os.path.join(args.drivenact_path, 'drivenact.json')
        with open(metadata_path, 'r') as file:
            metadata = json.loads(file.read())
        video_uids = []
        for video in metadata['videos']:
            if video['participant_id'] == participant_id:
                video_uids += [video['video_uid']]
        return video_uids

    def filter_annotations(video_uids: List[int]) -> Dict[str, List[Any]]:
        """Filter annotations for given video UIDs."""
        annotations_path = os.path.join(args.drivenact_path, 'annotations', 'narration.json')
        with open(annotations_path, 'r') as file:
            annotations = json.loads(file.read())
        result = {}
        for video_uid in video_uids:
            if video_uid not in annotations:
                continue
            annoted = annotations[video_uid]
            filtered = []
            for narrator_id in range(1, 4):
                if f'narration_pass_{narrator_id}' in annoted:
                    contents = annoted[f'narration_pass_{narrator_id}']
                    if 'narrations' in contents:
                        for narration in contents['narrations']:
                            filtered += [{'text': narration['narration_text'], 'timestamp_sec': narration['timestamp_sec']}]
            filtered.sort(key=lambda x: x['timestamp_sec'])
            result[video_uid] = filtered
        return result

    def choose_timestamps(contents: Dict[str, List[Any]]) -> Dict[str, Any]:
        """Choose timestamps for memory extraction."""
        sys_prompt = """You are provided with a textual narration from an drive&act first-person video. Your task is to carefully analyze this narration and identify timestamps that contain moments ideal for extracting meaningful personal data or details suitable for building a comprehensive personal profile or memory.

Criteria for selecting and ranking timestamps:

1. Personal Detail Relevance: Prioritize timestamps that provide specific details about the individual or related persons (e.g., personal preferences, relationships, owned items, frequent habits).
2. Clarity and Completeness: Prefer timestamps where the narration explicitly and clearly mentions identifiable personal data, ensuring accuracy in the extracted information.
3. Specificity and Usefulness: Identify timestamps with highly specific information that directly contributes to an informative personal dataset.

Please follow these steps:

1. Extract and list each candidate timestamp clearly from the narration.
2. Briefly summarize the personal data or details described at each timestamp.
3. Assign a quality score (1-10, with 10 being the highest) based on the outlined criteria.
4. Rank these timestamps in descending order of their quality scores, clearly explaining your justification for each score based on the criteria above.

Respond strictly in the following JSON format:

{
  "timestamps": [
    {
      "timestamp": "X (follow format from timestamp_sec)",
      "summary": "Brief summary of the personal detail",
      "quality_score": 1-10,
      "justification": "Explanation based on personal detail relevance, clarity, completeness, specificity, and usefulness"
    },
    {
      "timestamp": "X (follow format from timestamp_sec)",
      "summary": "Brief summary",
      "quality_score": 1-10,
      "justification": "Explanation"
    }
    // Add more entries as necessary
  ]
}

NOW: Given the following narrations JSON, output the required JSON ONLY. Do not output anything else (no explanations, no notes). Make sure your JSON is correct (no syntatic errors).

Do _not_ use any triple-backtick fenced code blocks or language identifiers."""
        
        cache_path = get_cache_path('choose')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        for video_uid, narrations in tqdm(contents.items(), desc=f'{pid} choose_timestamps', leave=True):
            if video_uid in cache:
                continue
            user_prompt = {'narrations': narrations}
            try:
                output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, []))
            except RetryError:
                output = {'timestamps': []}
            
            for item in output['timestamps']:
                temp = narrations.copy()
                temp.sort(key=lambda caption: abs(caption['timestamp_sec'] - float(item['timestamp'])))
                item['narration_text'] = temp[0]['text']
            cache[video_uid] = output['timestamps']
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, 'w') as file:
                file.write(json.dumps(cache, indent=2))
        return cache

    def generate_t1_memories(contents: Dict[str, Any]) -> List[Any]:
        """Generate type 1 memories from video timestamps."""
        sys_prompt = """You are a Persona Synthesizer tasked with building a richly detailed personal profile by analyzing drive&act-style narrations and video segments extracted from a single person's exocentric videos.

INPUT FORMAT:
{
  "previous_persona": [ … ]  // either a valid persona JSON from an earlier batch, or null if this is the first batch
  "narrations": {
    "<narrations>",
    ...
  }
}

and the video segment itself.

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
    [
        {
            "persona": "",
            "justification": ""
        },
        ...
    ]
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.
- Each "persona" should clearly reflect actionable personal details (e.g., personal preferences, owned items, relationships, specific events).
- These personas should not be a history of the actor's actions (e.g. I am in a store, I am talking to X), but instead should be personalized details (e.g. I have a black wallet, I love eating durian).
- Each "justification" should clearly indicate the reason for selecting this persona, referencing both the narrations and described visual content.
- Populate the JSON with as many relevant personas as possible.

INSTRUCTIONS:
1. Read the input JSON.  
2. If "previous_persona" is not null, you may reference it to inform the selection of relevant personas, but do not output persona details directly
3. For each video segment, analyze both the provided narrations and visual descriptions.
4. Identify explicit or implicit facts that would form meaningful personal memories. Generate as many persona as possible. The more the better.
5. Format the identified details into the output schema as a list of personalized memory objects with justifications. Make the personalized memory as detailed as possible.
6. Ensure clarity, conciseness, and relevance in each personalized memory and justification.
7. Produce exactly one JSON object conforming to the schema above—this is your new persona.
8. Do _not_ output anything else (no explanations, no notes).  
9. Use double quotes for all keys and string values. No comments, no extra keys, no prose.

Example output:

[
    {
        "persona": "My girlfriend likes durian.",
        "justification": "The narration mentions 'buying durian for my girlfriend,' and the video shows me selecting durian from a market."
    },
    {
        "persona": "My car is a Hyundai Elantra 2009 blue.",
        "justification": "The narration explicitly states, 'I am driving my Hyundai Elantra,' and the visual content confirms the blue 2009 model parked outside my house."
    },
]

NOW: Given the following narrations JSON, output the persona JSON only."""
        
        cache_path = get_cache_path('t1_gen')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        previous_persona = []
        result = []
        for video_uid, timestamps in tqdm(contents.items(), desc=f'{pid} generate_t1_memories', leave=True):
            for timestamp in tqdm(timestamps, desc=f'{video_uid}', leave=False):
                if video_uid in cache and timestamp['timestamp'] in cache[video_uid]:
                    cached = cache[video_uid][timestamp['timestamp']]
                    result += [cached]
                    before = json.loads(json.dumps(cached['result']))
                    for memory in before:
                        memory.pop('justification', None)
                    previous_persona += before
                    continue
                
                user_prompt = {'previous_persona': previous_persona, 'narrations': [timestamp['narration_text']]}
                try:
                    video_path = get_video_path(video_uid)
                    output = run_inference(args.model_id, supply_completion(
                        sys_prompt, user_prompt, 
                        load_video(video_path, float(timestamp['timestamp']), 1)
                    ))
                except RetryError:
                    output = []
                except Exception as e:
                    print(f"Error processing {video_uid} at {timestamp['timestamp']}: {e}")
                    output = []
                
                output = {
                    'video_id': video_uid, 
                    'timestamp': timestamp['timestamp'], 
                    'caption': [timestamp['narration_text']], 
                    'result': output
                }
                result += [output]
                if video_uid not in cache:
                    cache[video_uid] = {}
                cache[video_uid][timestamp['timestamp']] = output
                before = json.loads(json.dumps(output['result']))
                for memory in before:
                    memory.pop('justification', None)
                previous_persona += before
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, 'w') as file:
                    file.write(json.dumps(cache, indent=2))
        return result

    @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=32))
    def speculate_t1_memories(contents: List[Any]) -> List[Any]:
        """Speculate additional type 1 memories."""
        sys_prompt = """You are a Persona Speculator. Your task is to examine a JSON array, filled with personas extracted from a user's exocentric videos, grouped by the video id and timestamp. Your task is to generate additional personas on each group by speculation. You are to generate multiple new personas for each group. These generated personas should:

1. Be tightly related to the other personas in that group.
2. Not conflict with any information in all of the other personas combined.
3. Offer a plausible background, interests, or history.
4. Replace placeholder names such as "Man X" or "Woman Y" with realistic names. Make sure they are consistent across all the groups.
5. Use realistic names for any related persons, matching the cultural context.
6. Do not remove any groups. Process each one of them. Do not delete anything from the original personas.
7. Do not truncated for brevity.

INPUT:
A JSON object named "original_persona," in the format of:

[
  {
    "video_id": ...,
    "timestamp": ...,
    "caption": ...,
    "result": {
      [
        <personas extracted from video at timestamp>...
      ]
    }
  },
  ...
]

OUTPUT FORMAT:
Respond only with valid JSON with format identical to the input.
Do _not_ use any triple-backtick fenced code blocks or language identifiers."""
        
        cache_path = get_cache_path('t1_spec')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                return json.loads(file.read())
        
        user_prompt = []
        output = []
        for content in tqdm(contents, desc=f'{pid} speculate_t1_memories'):
            user_prompt += [content]
            if len(user_prompt) > 30:
                try:
                    for attempt in Retrying(stop=stop_after_attempt(13)):
                        with attempt:
                            try:
                                result = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, []))
                            except RetryError:
                                result = user_prompt
                            assert(len(result) == len(user_prompt))
                            for i in range(len(result)):
                                assert(result[i]['timestamp'] == user_prompt[i]['timestamp'])
                except RetryError:
                    result = user_prompt
                output += result
                user_prompt = []
        
        try:
            for attempt in Retrying(stop=stop_after_attempt(13)):
                with attempt:
                    try:
                        result = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, []))
                    except RetryError:
                        result = user_prompt
                    assert(len(result) == len(user_prompt))
                    for i in range(len(result)):
                        assert(result[i]['timestamp'] == user_prompt[i]['timestamp'])
        except RetryError:
            result = user_prompt
        output += result
        
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, 'w') as file:
            file.write(json.dumps(output, indent=2))
        return output

    def generate_t2_memories(contents: Dict[str, Any]) -> List[Any]:
        """Generate type 2 memories."""
        sys_prompt = """You are a Memory Builder, capable of building random memories based on items in the given video.

INPUT FORMAT:
["<memory>", // more memories]

OUTPUT FORMAT:
- Respond _only_ with valid JSON, following this exact schema:
    ["<memory>", // more memories]
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.
- Populate the JSON with as many relevant memories as possible.
- Each "persona" should clearly reflect actionable personal details (e.g., personal preferences, owned items, relationships, specific events).

INSTRUCTIONS:
1. Watch the video.
2. Read the input JSON. The input is a list of memories generated by AI. The generated memories from this prompt should not contradict or be similar to input memories.
3. Identify items from the video and generate random memories about these items. The memory must be the history of that specific item that is not shown in the video. The history may be a good or bad experience. Specify the exact time and location of the memory if possible, for example, a week ago, last month, in my bench. Generate as many memories as possible. The more, the better.
4. Ensure clarity and conciseness in each personalized memory.
5. Produce exactly one JSON object conforming to the schema above—this is your new memory.
6. Do _not_ output anything else (no explanations, no notes).  
7. Use double quotes for all keys and string values. No comments, no extra keys, no prose.

Example output:

["I'm allergic to durian.", "I bought this wallet last week"]

NOW: Given the following narrations JSON, output the persona JSON only."""
        
        cache_path = get_cache_path('t2_gen')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        previous_persona = []
        result = []
        for video_uid, timestamps in tqdm(contents.items(), desc=f'{pid} generate_t2_memories', leave=True):
            for timestamp in tqdm(timestamps, desc=f'{video_uid}', leave=False):
                if video_uid in cache and timestamp['timestamp'] in cache[video_uid]:
                    cached = cache[video_uid][timestamp['timestamp']]
                    result += [cached]
                    before = json.loads(json.dumps(cached['result']))
                    previous_persona += before
                    continue
                
                while len(previous_persona) > 500:
                    previous_persona.pop(0)
                
                user_prompt = {'previous_persona': previous_persona, 'narrations': [timestamp['narration_text']]}
                try:
                    video_path = get_video_path(video_uid)
                    output = run_inference(args.model_id, supply_completion(
                        sys_prompt, user_prompt, 
                        load_video(video_path, float(timestamp['timestamp']), 1)
                    ))
                except RetryError:
                    output = []
                except Exception:
                    output = []
                
                output = {
                    'video_id': video_uid, 
                    'timestamp': timestamp['timestamp'], 
                    'caption': [timestamp['narration_text']], 
                    'result': output
                }
                result += [output]
                if video_uid not in cache:
                    cache[video_uid] = {}
                cache[video_uid][timestamp['timestamp']] = output
                before = json.loads(json.dumps(output['result']))
                previous_persona += before
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, 'w') as file:
                    file.write(json.dumps(cache, indent=2))
        return result

    def generate_qa(contents: List[Any]) -> List[Any]:
        """Generate question-answer pairs."""
        sys_prompt = """You are a Assistant Thinker, capable of creating relevant and realistic question-answer interaction between user and AI assistant based on exocentric video and list of memories.

INPUT:
{
  "type_1_memories": ["<memory>", // more memories],
  "type_2_memories": ["<memory>", // more memories]
}

and a clip of the video

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  [
    {
      "question": "<question>",
      "answer": "<answer>",
      "justification": "<justification>"
    },
    // Add more question-answer interactions
  ]
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.

INSTRUCTION:
- Analyze the video and memories carefully.
- "type_1_memories" is a list of type 1 memories and "type_2_memories" is a list of type 2 memories. All of them are user's memories, not your memories.
- Find the intersection between video and memories. Generate first-person point-of-view questions based on intersection. The questions should be realistic. The question must need both type of memories to generate the answer.
- Generate answer of the question as the assistant of the user.
- Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory.
- Give the reason of why the questions can only be answered by combining both types of memories. Ensure that type 1 only memory and type 2 only memory is not enough to answer the question.
- If there is no valid question, output an empty array.

EXAMPLE:
[
  {
    "question": "Where is my fried chicken flour?",
    "answer": "It is in the cupboard on your left-hand side.",
    "justification": "The user is searching for something with raw chicken nearby (V). From memory (M1.4), the fried chicken flour is in the cupboard. The user may ask, \"Where is my fried chicken flour?\""
  }
]"""
        
        cache_path = get_cache_path('qa')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_qa', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            cache_key = f'{video_id}|{timestamp}'
            if cache_key in cache:
                result += [cache[cache_key]]
                continue
            
            user_prompt = {
                'type_1_memories': content['type_1_memories'], 
                'type_2_memories': content['type_2_memories']
            }
            try:
                video_path = get_video_path(video_id)
                output = run_inference(args.model_id, supply_completion(
                    sys_prompt, user_prompt, 
                    load_video(video_path, float(timestamp), 1)
                ))
            except RetryError:
                output = []
            except Exception:
                output = []
            
            output = {**content, 'result': output}
            cache[cache_key] = output
            result += [output]
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, 'w') as file:
                file.write(json.dumps(cache, indent=2))
        return result

    def generate_proactive(contents: List[Any]) -> List[Any]:
        """Generate proactive responses."""
        sys_prompt = """INPUT:
{
  "type_1_memories": ["<memory>", // more memories],
  "type_2_memories": ["<memory>", // more memories]
}

and a clip of the video

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  [
    {
      "response": "<response>",
      "justification": "<justification>",
      "urgency": <urgency>
    },
    // Add more proactive-response interactions
  ]
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.

INSTRUCTION:
- Analyze the video and memories carefully. The video is an exocentric vision of me who uses an AI assistant.
- "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
- Find visual information. Generate proactive responses based on the visual information. Use given memories to analyze the degree of urgency.
- Provide justification of why you need to interfere the current activity with a proactive response and why this is urgent. Only use the video and memories as the ground truth.
- Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory.
- Provide an urgency level from 0 to 10 that the assistant needs to interfere with the activity with a proactive response.
- Sort the proactive response based on urgency level.
- If there is no valid response, output an empty array."""
        
        cache_path = get_cache_path('proactive')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_proactive', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            cache_key = f'{video_id}|{timestamp}'
            if cache_key in cache:
                result += [cache[cache_key]]
                continue
            
            user_prompt = {
                'type_1_memories': content['type_1_memories'], 
                'type_2_memories': content['type_2_memories']
            }
            try:
                video_path = get_video_path(video_id)
                video_frame = load_video(video_path, float(timestamp), 1)
                output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, video_frame))
            except RetryError:
                output = []
            except Exception:
                output = []
            
            output = {**content, 'result': output}
            cache[cache_key] = output
            result += [output]
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, 'w') as file:
                file.write(json.dumps(cache, indent=2))
        return result

    def generate_classification(contents: List[Any]) -> List[Any]:
        """Generate classification (speak/silent) decisions."""
        sys_prompt = """INPUT:
{
  "type_1_memories": ["<memory>", // more memories],
  "type_2_memories": ["<memory>", // more memories]
}

and a clip of the video

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  {
    "token": "<speak/silent>"
  }
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.

INSTRUCTION:
- Analyze the video and memories carefully. The video is an exocentric vision of me who uses an AI assistant.
- "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
- Determine whether it is time to speak proactively or keep silent. Keep the balance between providing useful information or prevention and annoyance of the user.
- Return "speak" or "silent" in the token field."""
        
        cache_path = get_cache_path('classification')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_classification', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            cache_key = f'{video_id}|{timestamp}'
            if cache_key in cache:
                result += [cache[cache_key]]
                continue
            
            user_prompt = {
                'type_1_memories': content['type_1_memories'], 
                'type_2_memories': content['type_2_memories']
            }
            try:
                video_path = get_video_path(video_id)
                video_frame = load_video(video_path, float(timestamp), 1)
                output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, video_frame))
            except RetryError:
                output = {'token': 'invalid'}
            except Exception:
                output = {'token': 'invalid'}
            
            output = {**content, 'token': output['token']}
            cache[cache_key] = output
            result += [output]
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, 'w') as file:
                file.write(json.dumps(cache, indent=2))
        return result

    def generate_ans_and_grd(contents: List) -> List[Any]:
        """Generate answers and grades."""
        ans_prompt = """You are an Answerer Master capable of answering any question based on given video and memories

INPUT FORMAT:
{
  "caption": "<caption>",
  "memories": ["<memories>", ...],
  "question": "<question>"
},

and a clip of the video near the timestamp

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  {
    "answer": "<answer>" 
  }
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.

INSTRUCTION:
- Only uses the given information to answer the question. Do _not_ speculate any information if not given.

EXAMPLE OUTPUT:
{
  "answer": "Durian" 
}"""
        
        grd_prompt = """You are a Grader Master capable of grading the given answer based on the problem and one of the correct answer. 

INPUT FORMAT:
{
  "question": "<question>",
  "correct_answer": "<correct_answer>",
  "answer": "<answer>"
},

and a clip of the video near the timestamp

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  {
    "correctness": <0-10>,
    "justification": "<justification>" 
  }
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.

EXAMPLE OUTPUT:
{
  "correctness": 9,
  "justification": "The answer is same with the correct answer, therefore it is correct." 
}

INSTRUCTION:
- Give correctness mark in floating point from 0 to 10 where 0 is absolutely incorrect and 10 is absolutely correct.
- Use "(Mx.y)" format to refer a memory in justification where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory."""
        
        cache_path = get_cache_path('ans_and_grd')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_ans_and_grd', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            caption = content['caption']
            memories = content['type_1_memories'] + content['type_2_memories']
            
            for qa in content['result']:
                question = qa['question']
                correct_answer = qa['answer']
                cache_key = f'{video_id}|{timestamp}|{question}'
                if cache_key in cache:
                    result += [cache[cache_key]]
                    continue
                
                # Generate answer with memories
                user_prompt = {'caption': caption, 'memories': memories, 'question': question}
                try:
                    video_path = get_video_path(video_id)
                    vt1t2_answer = run_inference(
                        args.model_id, 
                        supply_completion(ans_prompt, user_prompt, load_video(video_path, float(timestamp), 1))
                    )['answer']
                except (RetryError, KeyError, Exception):
                    vt1t2_answer = 'No answer'
                
                # Generate answer without memories
                user_prompt = {'caption': caption, 'memories': [], 'question': question}
                try:
                    video_path = get_video_path(video_id)
                    v_answer = run_inference(
                        args.model_id, 
                        supply_completion(ans_prompt, user_prompt, load_video(video_path, float(timestamp), 1))
                    )['answer']
                except (RetryError, KeyError, Exception):
                    v_answer = 'No answer'
                
                # Grade vt1t2 answer
                user_prompt = {'question': question, 'correct_answer': correct_answer, 'answer': vt1t2_answer}
                try:
                    vt1t2_grd = run_inference(args.model_id, supply_completion(grd_prompt, user_prompt, []))
                    vt1t2_correctness = vt1t2_grd['correctness']
                    vt1t2_justification = vt1t2_grd['justification']
                except (RetryError, KeyError, Exception):
                    vt1t2_correctness = 0
                    vt1t2_justification = 'NA'
                
                # Grade v-only answer
                user_prompt = {'question': question, 'correct_answer': correct_answer, 'answer': v_answer}
                try:
                    v_grd = run_inference(args.model_id, supply_completion(grd_prompt, user_prompt, []))
                    v_correctness = v_grd['correctness']
                    v_justification = v_grd['justification']
                except (RetryError, KeyError, Exception):
                    v_correctness = 0
                    v_justification = 'NA'
                
                output = {
                    **content, 
                    'question': question, 
                    'answer': correct_answer, 
                    'vt1t2_answer': vt1t2_answer, 
                    'vt1t2_correctness': vt1t2_correctness, 
                    'vt1t2_justification': vt1t2_justification, 
                    'v_answer': v_answer, 
                    'v_correctness': v_correctness, 
                    'v_justification': v_justification
                }
                output.pop('result', None)
                cache[cache_key] = output
                result += [output]
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, 'w') as file:
                    file.write(json.dumps(cache, indent=2))
        return result

    def generate_proactive_evaluation(contents: List) -> List[Any]:
        """Generate proactive evaluation scores."""
        sys_prompt = """You are an evaluator (LLM) tasked with judging whether another LLM's response at a specific timestamp is necessary.

INPUT:
{
  "caption": ["<caption>", ...]
  "type_1_memories": ["<memory>", // more memories],
  "type_2_memories": ["<memory>", // more memories],
  "response": "<response>"
}

and a clip of the video

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  {
    "score": "<score>",
    "justification": "<justification>",
  },
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.
INSTRUCTION:
- Analyze the video and memories carefully. The video is an exocentric vision of me who uses an AI assistant.
- "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
- Give score from 0 to 5 whether the proactive response is necessary. You can assume that the response is given at the end of the clip. Provide justification on your scoring.
- Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory."""
        
        cache_path = get_cache_path('proactive_evaluation')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_proactive_evaluation', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            caption = content['caption']
            t1_memories = content['type_1_memories']
            t2_memories = content['type_2_memories']
            
            for proactive in content['result']:
                response = proactive['response']
                urgency = proactive['urgency']
                cache_key = f'{video_id}|{timestamp}|{response}'
                if cache_key in cache:
                    result += [cache[cache_key]]
                    continue
                
                user_prompt = {
                    'caption': caption, 
                    'type_1_memories': t1_memories, 
                    'type_2_memories': t2_memories, 
                    'response': response
                }
                try:
                    video_path = get_video_path(video_id)
                    output = run_inference(
                        args.model_id, 
                        supply_completion(sys_prompt, user_prompt, load_video(video_path, float(timestamp), 1))
                    )
                    output = {
                        **content, 
                        'response': response, 
                        'evaluator_grade': output['score'], 
                        'evaluator_justification': output['justification']
                    }
                except Exception:
                    output = {
                        **content, 
                        'response': response, 
                        'evaluator_grade': '0', 
                        'evaluator_justification': 'Error during evaluation'
                    }
                
                output.pop('result', None)
                cache[cache_key] = output
                result += [output]
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, 'w') as file:
                    file.write(json.dumps(cache, indent=2))
        return result

    def generate_justfication_understanding(contents: List) -> List[Any]:
        """Generate justification understanding."""
        sys_prompt = """INPUT:
{
  "reasoning": "<reasoning>"
}

OUTPUT SPECIFICATION:
- Respond _only_ with valid JSON, following this exact schema:
  {
    "response": "<speak/silent>",
  },
- Do _not_ use any triple-backtick fenced code blocks or language identifiers.
INSTRUCTION:
- Analyze the given reasoning. Determine whether you need to give proactive response or not. Use necessity as the parameter.
- Return speak if proactive response is needed, and silent otherwise."""
        
        cache_path = get_cache_path('justification_understanding')
        if os.path.isfile(cache_path):
            with open(cache_path, 'r') as file:
                cache = json.loads(file.read())
        else:
            cache = {}
        
        result = []
        for content in tqdm(contents, desc=f'{pid} generate_justification_understanding', leave=True):
            video_id = content['video_id']
            timestamp = content['timestamp']
            caption = content['caption']
            t1_memories = content['type_1_memories']
            t2_memories = content['type_2_memories']
            
            for proactive in content['result']:
                response = proactive['response']
                reasoning = proactive['justification']
                urgency = proactive['urgency']
                if urgency < 8:
                    continue
                
                cache_key = f'{video_id}|{timestamp}|{response}'
                if cache_key in cache:
                    result += [cache[cache_key]]
                    continue
                
                user_prompt = {'reasoning': response}
                try:
                    video_path = get_video_path(video_id)
                    output = run_inference(
                        args.model_id, 
                        supply_completion(sys_prompt, user_prompt, load_video(video_path, float(timestamp), 1))
                    )
                    output = {**content, 'response': response, 'should_response': output['response']}
                except Exception:
                    output = {**content, 'response': response, 'should_response': 'silent'}
                
                output.pop('result', None)
                cache[cache_key] = output
                result += [output]
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, 'w') as file:
                    file.write(json.dumps(cache, indent=2))
        return result

    def supply_completion(sys_prompt: str, user_prompt: Dict[str, Any], frames: List[Any]) -> List[Dict[str, str]]:
        """Supply completion messages for API."""
        return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": json.dumps(user_prompt)}] + \
            ([{"role": "user", "content": build_image_messages(frames)}] if len(frames) else [])

    @retry(stop=stop_after_attempt(13), wait=wait_random(min=2, max=4))
    def run_inference(model, input) -> str:
        """Run inference with retry logic."""
        try:
            response = openai.ChatCompletion.create(model=model, messages=input)
            output = json.loads(response.choices[0].message.content)
            return output
        except Exception as e:
            raise EOFError

    def load_video(video_path: str, timestamp: float, target_fps: int) -> List[str]:
        """Load video frames around timestamp."""
        frames = sample_frames(
            video_path, 
            timestamp - args.video_start_before, 
            timestamp + args.video_end_after, 
            target_fps
        )
        b64_images = [frame_to_base64_png(f) for f in frames]
        return b64_images

    def sample_frames(path: str, start: float, end: float, target_fps: float) -> List[Any]:
        """Sample frames from video."""
        frames, _, info = torchvision.io.read_video(path, start_pts=start, end_pts=end, pts_unit="sec")
        native_fps = info["video_fps"]
        stride = max(int(round(native_fps / target_fps)), 1)
        sampled = frames[::stride]
        return [frame for frame in sampled]

    def frame_to_base64_png(frame: Any) -> str:
        """Convert frame to base64 PNG."""
        img = Image.fromarray(frame.numpy())
        (width, height) = (img.width // args.scale_down, img.height // args.scale_down)
        img_new = img.resize((width, height))
        buf = io.BytesIO()
        img_new.save(buf, format="PNG", optimize=False)
        return base64.b64encode(buf.getvalue()).decode('utf-8')

    def build_image_messages(b64_list: List[str]) -> List[Any]:
        """Build image messages for API."""
        return [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}} for b64 in b64_list]

    # Main processing pipeline
    # Load annotations from CSV
    annotations = []
    with open(args.annotations_csv, 'r') as file:
        csv_file = csv.DictReader(file)
        for row in csv_file:
            annotations += [row]

    # Process annotations
    vid = {}
    fmin = {}
    fmax = {}
    task = {}

    for annotation in annotations:
        participant_id = annotation['participant_id']
        file_id = annotation['file_id']
        annotation_id = int(annotation['annotation_id'])
        frame_start = int(annotation['frame_start'])
        frame_end = int(annotation['frame_end'])
        activity = annotation['activity']
        
        if participant_id not in vid:
            vid[participant_id] = []
        vid[participant_id] += [file_id]
        
        if file_id not in fmin:
            fmin[file_id] = {}
            fmax[file_id] = {}
            task[file_id] = {}
        
        if annotation_id not in fmin[file_id]:
            fmin[file_id][annotation_id] = float('inf')
            fmax[file_id][annotation_id] = -1
            task[file_id][annotation_id] = activity
        
        fmin[file_id][annotation_id] = min(fmin[file_id][annotation_id], frame_start)
        fmax[file_id][annotation_id] = max(fmax[file_id][annotation_id], frame_end)

    # Build content dictionary
    ctn = {}
    for pid_key, vs in vid.items():
        ctn[pid_key] = {}
        for v in vs:
            ctn[pid_key][v] = []
            for i, _ in fmin[v].items():
                ctn[pid_key][v] += [{
                    'timestamp_sec': fmin[v][i] / args.fps, 
                    'narration_text': task[v][i].replace('_', ' ')
                }]

    # Process for this participant
    if str(pid) not in ctn:
        print(f"Warning: Participant {pid} not found in annotations")
        return
    
    timestamps = choose_timestamps(ctn[str(pid)])
    t1_memories = speculate_t1_memories(generate_t1_memories(timestamps))
    t2_memories = generate_t2_memories(timestamps)
    
    # Merge memories
    merged = []
    assert len(t1_memories) == len(t2_memories), f"Mismatch: {len(t1_memories)} != {len(t2_memories)}"
    
    for index in range(len(t1_memories)):
        assert t1_memories[index]['video_id'] == t2_memories[index]['video_id']
        assert str(t1_memories[index]['timestamp']) == str(t2_memories[index]['timestamp'])
        
        temp = {
            'video_id': t1_memories[index]['video_id'], 
            'timestamp': t1_memories[index]['timestamp'], 
            'caption': t1_memories[index]['caption']
        }
        type_1_memories = []
        for item in t1_memories[index]['result']:
            type_1_memories += [item['persona']]
        type_2_memories = t2_memories[index]['result']
        temp['type_1_memories'] = type_1_memories
        temp['type_2_memories'] = type_2_memories
        merged += [temp]
    
    # Generate QA pairs
    result = generate_ans_and_grd(generate_qa(merged))
    
    # Filter results
    invalid = 0
    passed = []
    for item in result:
        if item['vt1t2_correctness'] < args.vt1t2_correctness_threshold:
            invalid += 1
        elif item['v_correctness'] < args.v_correctness_threshold:
            passed += [item]
    
    # Save passive dataset
    os.makedirs(passive_output_dir, exist_ok=True)
    print(f'{pid}: {invalid}/{len(result)} QA pair invalid')
    print(f'{pid}: {len(passed)}/{len(result)} QA pair generated')
    with open(os.path.join(passive_output_dir, str(pid)), 'w') as file:
        file.write(json.dumps(passed, indent=2))
    
    # Generate proactive dataset
    pv = generate_justfication_understanding(generate_proactive(generate_classification(merged)))
    os.makedirs(proactive_output_dir, exist_ok=True)
    with open(os.path.join(proactive_output_dir, str(pid)), 'w') as file:
        file.write(json.dumps(pv, indent=2))


if __name__ == '__main__':
    args = parse_args()
    
    # Determine participant IDs
    if args.participant_ids:
        participant_ids = [int(pid.strip()) for pid in args.participant_ids.split(',')]
    else:
        # Auto-detect from CSV
        participant_ids = set()
        with open(args.annotations_csv, 'r') as file:
            csv_file = csv.DictReader(file)
            for row in csv_file:
                participant_ids.add(int(row['participant_id']))
        participant_ids = sorted(participant_ids)
    
    print(f"Processing {len(participant_ids)} participants: {participant_ids}")
    
    # Process in parallel
    with Pool(processes=args.num_workers) as pool:
        pool.map(generate_enc, [(args, pid) for pid in participant_ids])
