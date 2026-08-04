import argparse
import base64
import io
import json
import os
import openai
from pathlib import Path
from tenacity import Retrying, RetryError, retry, stop_after_attempt, wait_random
import torchvision
from PIL import Image
from typing import Any, Dict, List
import time

from tqdm import tqdm

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Generate Ego4D dataset with passive and proactive QA pairs")
    
    # Required paths
    parser.add_argument('--ego4d_path', type=str, required=True,
                       help='Path to Ego4D dataset root directory')
    parser.add_argument('--ego4d_dataset', type=str, required=True,
                       help='Name of the Ego4D dataset subdirectory')
    
    # Optional paths
    parser.add_argument('--cache_dir', type=str, default='.cache_ego4d',
                       help='Directory for caching intermediate results')
    parser.add_argument('--output_dir', type=str, default='.',
                       help='Output directory for generated datasets')
    parser.add_argument('--passive_output_dir', type=str, default=None,
                       help='Output directory for passive dataset (default: {output_dir}/passive_ego4d)')
    parser.add_argument('--proactive_output_dir', type=str, default=None,
                       help='Output directory for proactive dataset (default: {output_dir}/proactive_ego4d)')
    
    # Model and API configuration
    parser.add_argument('--model_id', type=str, default='openai/gpt-4o-mini',
                       help='Model ID for API calls')
    parser.add_argument('--api_key', type=str, default=None,
                       help='OpenAI API key (default: from OPENAI_API_KEY env var)')
    parser.add_argument('--api_base', type=str, default=None,
                       help='API base URL (default: from OPENAI_API_BASE env var or OpenAI standard)')
    
    # Video processing parameters
    parser.add_argument('--scale_down', type=int, default=1,
                       help='Scale down factor for video frames')
    parser.add_argument('--video_start_before', type=float, default=5.0,
                       help='Seconds to extract before timestamp')
    parser.add_argument('--video_end_after', type=float, default=2.0,
                       help='Seconds to extract after timestamp')
    
    # Processing parameters
    parser.add_argument('--participant_ids', type=str, default=None,
                       help='Comma-separated list of participant IDs (default: use hardcoded set)')
    parser.add_argument('--num_workers', type=int, default=8,
                       help='Number of parallel workers')
    
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
    else:
        openai.api_base = os.getenv('OPENAI_API_BASE', "https://api.openai.com/v1")
    
    # Setup paths
    cache_dir = os.path.join(args.cache_dir, str(pid))
    passive_output_dir = args.passive_output_dir or os.path.join(args.output_dir, 'passive_ego4d')
    proactive_output_dir = args.proactive_output_dir or os.path.join(args.output_dir, 'proactive_ego4d')
    
    def get_cache_path(cache_name: str) -> str:
        """Get path to cache file."""
        return os.path.join(cache_dir, cache_name)
    
    def get_video_path(video_uid: str) -> str:
        """Get full path to video file."""
        return os.path.join(args.ego4d_path, 'v2', args.ego4d_dataset, f"{video_uid}.mp4")
    
    def generate_enc_inner(pid: int):
      def filter_video_uids(fb_participant_id: int) -> List[int]:
        metadata_path = os.path.join(args.ego4d_path, 'ego4d.json')
        with open(metadata_path, 'r') as file:
          metadata = json.loads(file.read())
        video_uids = []
        for video in metadata['videos']:
          video_path = get_video_path(video['video_uid'])
          if video['fb_participant_id'] == fb_participant_id and Path(video_path).is_file():
            video_uids += [video['video_uid']]
        return video_uids

      def filter_annotations(video_uids: List[int]) -> Dict[str, List[Any]]:
        annotations_path = os.path.join(args.ego4d_path, 'v2', 'annotations', 'narration.json')
        with open(annotations_path, 'r') as file:
      annotations = json.loads(file.read())
    result = {}
    for video_uid in video_uids:
      annoted = annotations[video_uid]
      filtered = []
      for narrator_id in range(1, 4):
        if f'narration_pass_{narrator_id}' in annoted:
          contents = annoted[f'narration_pass_{narrator_id}']
          if 'narrations' in contents:
            for narration in contents['narrations']:
              filtered += [{'text': narration['narration_text'], 'timestamp_sec': narration['timestamp_sec']}]
      filtered.sort(key=lambda x : x['timestamp_sec'])
      result[video_uid] = filtered
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def choose_timestamps(contents: Dict[str, List[Any]]) -> Dict[str, Any]:
    sys_prompt = """You are provided with a textual narration from an Ego4D first-person video. Your task is to carefully analyze this narration and identify timestamps that contain moments ideal for extracting meaningful personal data or details suitable for building a comprehensive personal profile or memory.

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
    if os.path.isfile(get_cache_path('choose')):
      with open(get_cache_path('choose'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    for video_uid, narrations in tqdm(contents.items(), desc=f'{pid} choose_timestamps', leave=False):
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
      with open(get_cache_path('choose'), 'w') as file:
        file.write(json.dumps(cache, indent=2))
    return cache

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_t1_memories(contents: Dict[str, Any]) -> List[Any]:
    sys_prompt = """You are a Persona Synthesizer tasked with building a richly detailed personal profile by analyzing ego4d-style narrations and video segments extracted from a single person’s egocentric videos.

  INPUT FORMAT:
  {
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
  2. For each video segment, analyze both the provided narrations and visual descriptions.
  3. Identify explicit or implicit facts that would form meaningful personal memories. Generate as many persona as possible. The more the better.
  4. Format the identified details into the output schema as a list of personalized memory objects with justifications. Make the personalized memory as detailed as possible.
  5. Ensure clarity, conciseness, and relevance in each personalized memory and justification.
  6. Produce exactly one JSON object conforming to the schema above—this is your new persona.
  7. Do _not_ output anything else (no explanations, no notes).  
  8. Use double quotes for all keys and string values. No comments, no extra keys, no prose.

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
        if os.path.isfile(get_cache_path('t1_gen')):
          with open(get_cache_path('t1_gen'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    previous_persona = []
    result = []
    for video_uid, timestamps in tqdm(contents.items(), desc=f'{pid} generate_t1_memories', leave=False):
      for timestamp in tqdm(timestamps, desc=f'{pid} {video_uid}', leave=False):
        if video_uid in cache and timestamp['timestamp'] in cache[video_uid]:
          cached = cache[video_uid][timestamp['timestamp']]
          result += [cached]
          before = json.loads(json.dumps(cached['result']))
          for memory in before:
            memory.pop('justification')
          previous_persona += before
          continue
        user_prompt = {'narrations': [timestamp['narration_text']]}
        try:
              output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, load_video(get_video_path(video_uid), float(timestamp['timestamp']), 1)))
        except RetryError:
          output = []
        except KeyError:
          output = []
        output = {'video_id': video_uid, 'timestamp': timestamp['timestamp'], 'caption': [timestamp['narration_text']], 'result': output}
        result += [output]
        if video_uid not in cache:
          cache[video_uid] = {}
        cache[video_uid][timestamp['timestamp']] = output
        before = json.loads(json.dumps(output['result']))
        for memory in before:
          memory.pop('justification')
        previous_persona += before
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('t1_gen'), 'w') as file:
          file.write(json.dumps(cache, indent=2))
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def speculate_t1_memories(contents: List[Any]) -> List[Any]:
    sys_prompt = """You are a Persona Speculator. Your task is to examine a JSON array, filled with personas extracted from a user's egocentric videos, grouped by the video id and timestamp. Your task is to generate additional personas on each group by speculation. You are to generate multiple new personas for each group. These generated personas should:

  1. Be tightly related to the other personas in that group.
  2. Not conflict with any information in all of the other personas combined.
  3. Offer a plausible background, interests, or history.
  4. Replace placeholder names such as "Man X" or "Woman Y" with realistic names. Make sure they are consistent across all the groups.
  4. Use realistic names for any related persons, matching the cultural context.
  5. Do not remove any groups. Process each one of them. Do not delete anything from the original personas.
  6. Do not truncated for brevity.

  INPUT:
  A JSON object named “original_persona,” in the format of:


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

  For example:
  [
    {
      "video_id": "d730a47a-013d-4cd3-b478-370f2fb0d918", 
      "timestamp": 305.31910860000005, 
      "caption": ["#c c looks at his wallet"], 
      "result": [
        {
          "persona": "I have a black wallet.", 
          "justification": "The narration mentions 'c looks at his wallet,' and the video shows a black wallet being handled."
        }
      ]
    }, 
    {
      "video_id": "d730a47a-013d-4cd3-b478-370f2fb0d918", 
      "timestamp": 316.11839860000003, 
      "caption": ["#c c removes a piece of paper from his wallet"], 
      "result": [
        {
          "persona": "I keep important papers in my black wallet.", 
          "justification": "The narration mentions 'c removes a piece of paper from his wallet,' and the video shows a hand holding a piece of paper, indicating that the wallet contains important documents."
        }
      ]
    }
  ]

  OUTPUT FORMAT:
  Respond only with valid JSON with format identical to the input.
  Do _not_ use any triple-backtick fenced code blocks or language identifiers."""
        if os.path.isfile(get_cache_path('t1_spec')):
          with open(get_cache_path('t1_spec'), 'r') as file:
        return json.loads(file.read())
    else:
      user_prompt = []
      output = []
      for content in tqdm(contents, desc=f'{pid} speculate_t1_memories'):
        user_prompt += [content]
        if len(user_prompt) > 30:
          try:
            for attempt in Retrying(stop=stop_after_attempt(13)):
              with attempt:
                try:
                  result = user_prompt # run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, []))
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
          with open(get_cache_path('t1_spec'), 'w') as file:
        file.write(json.dumps(output, indent=2))
      return output

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_t2_memories(contents: Dict[str, Any]) -> List[Any]:
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
  2. Read the input JSON. The input is a list of memories generated by AI. The generated memories from this prompt should be consistent with input memories.
  3. Identify items from the video and generate random memories about these items. The memory must be the history of that specific item that is not shown in the video. The history may be a good or bad experience. Specify the exact time and location of the memory if possible, for example, a week ago, last month, in my bench.
  4. Ensure clarity and conciseness in each personalized memory.
  5. Produce exactly one JSON object conforming to the schema above—this is your new memory.
  6. Do _not_ output anything else (no explanations, no notes).  
  7. Use double quotes for all keys and string values. No comments, no extra keys, no prose.

  Example output:

  ["I'm allergic to durian.", "I bought this wallet last week"]

  NOW: Given the following narrations JSON, output the persona JSON only."""
        if os.path.isfile(get_cache_path('t2_gen')):
          with open(get_cache_path('t2_gen'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    previous_persona = []
    result = []
    for video_uid, timestamps in tqdm(contents.items(), desc=f'{pid} generate_t2_memories', leave=False):
      for timestamp in tqdm(timestamps, desc=f'{pid} {video_uid}', leave=False):
        if video_uid in cache and timestamp['timestamp'] in cache[video_uid]:
          cached = cache[video_uid][timestamp['timestamp']]
          result += [cached]
          before = json.loads(json.dumps(cached['result']))
          previous_persona += before
          continue
        while (len(previous_persona) > 2000):
          previous_persona.pop(0)
        user_prompt = {'previous_persona': previous_persona, 'narrations': [timestamp['narration_text']]}
        try:
              output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, load_video(get_video_path(video_uid), float(timestamp['timestamp']), 1)))
        except RetryError:
          output = []
        except KeyError:
          output = []
        output = {'video_id': video_uid, 'timestamp': timestamp['timestamp'], 'caption': [timestamp['narration_text']], 'result': output}
        result += [output]
        if video_uid not in cache:
          cache[video_uid] = {}
        cache[video_uid][timestamp['timestamp']] = output
        before = json.loads(json.dumps(output['result']))
        previous_persona += before
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('t2_gen'), 'w') as file:
          file.write(json.dumps(cache, indent=2))
    return result

  # def summarize_type_2_memories(contents: Dict[str, Any]) -> List[Any]:


  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_qa(contents: List[Any]) -> List[Any]:
    sys_prompt = """You are a Assistant Thinker, capable of creating relevant and realistic question-answer interaction between user and AI assistant based on egocentric video and list of memories.

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
        "justification": "<justification>",
        "reference": ["<reference>", ...]
      },
      // Add more question-answer interactions
    ]
  - Do _not_ use any triple-backtick fenced code blocks or language identifiers.

  INSTRUCTION:
  - Analyze the video and memories carefully.
  - "type_1_memories" is a list of type 1 memories and "type_2_memories" is a list of type 2 memories. All of them are user's memories, not your memories.
  - Find the intersection between video and memories. Generate first-person point-of-view questions based on intersection. The questions should be realistic. The question must need both type of memories to generate the answer.
  - Generate answer of the question as the assistant.
  - Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory. Use "(V)" to refer a video. Do _not_ use another format to refer a memory. Only put reference in reference and justification field.
  - If there is no valid question, output an empty array.
  - If there are more than one valid question, output one question that has highest quality.

  EXAMPLE:
  [
    {
      "question": "Where is my fried chicken flour?",
      "answer": "It is in the cupboard on your left-hand side.",
      "justification": "The user is searching for something with raw chicken nearby (V). From memory (M1.4), the fried chicken flour is in the cupboard. The user may ask, \"Where is my fried chicken flour?\".",
      "reference": ["(M1.4)", "(V)"]
    }
  ]

  []"""
        if os.path.isfile(get_cache_path('qa')):
          with open(get_cache_path('qa'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    type_1_memories = {}
    for content in tqdm(contents, desc=f'{pid} generate_qa', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      if video_id not in type_1_memories:
        type_1_memories[video_id] = []
      for i in range(len(content['type_1_memories'])):
        type_1_memories[video_id] += [content['type_1_memories'][i]]
      if f'{video_id}|{timestamp}' in cache:
        result += [cache[f'{video_id}|{timestamp}']]
        continue
      content['type_1_memories'] = type_1_memories[video_id].copy()
      user_prompt = {'type_1_memories': content['type_1_memories'], 'type_2_memories': content['type_2_memories']}
      try:
            output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, load_video(get_video_path(video_id), float(timestamp), 1)))
      except RetryError:
        output = []
      except KeyError:
        output = []
      output = {**content, 'result': output}
      cache[f'{video_id}|{timestamp}'] = output
      result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('qa'), 'w') as file:
        file.write(json.dumps(cache, indent=2))
    return result

  def generate_reasoning(contents: List[Any]) -> List[Any]:
    sys_prompt = """INPUT:
  {
    "type_1_memories": ["<memory>", // more memories],
    "type_2_memories": ["<memory>", // more memories]
  }

  and a clip of the video

  OUTPUT SPECIFICATION:
  - Respond _only_ with valid JSON, following this exact schema:
    {
      "reasoning": "<reasoning>",
      "type": "<passive/proactive/nil>",
      "reference": ["<reference>", ...]
    }
  - Do _not_ use any triple-backtick fenced code blocks or language identifiers.

  INSTRUCTION:
  - Analyze the video and memories carefully. The video is an egocentric vision of me who uses an AI assistant.
  - "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
  - Find visual information that has connection with the memories. Generate reasoning on possible interaction between AI assistant and the user, both passive and proactive interaction
  - Provide justification of why you need to interfere the current activity with a proactive response and why this is urgent. Only use the video and memories as the ground truth.
  - Provide type of the interaction, either passive, proactive, or nil.
  - Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory. Use "(V)" to refer a video. Do _not_ use another format to refer a memory.
  - If there are no memories can be inferenced, provide empty string in reasoning.
  - The number of proactive interaction type should not be more than 1/5 of all input.

  EXAMPLE:
  {
    "reasoning": "The user is about to add milk to the chicken (V), but prior memory (M2.3) shows he is allergic to milk. The AI assistant should promptly remind him not to use it for marinating.",
    "type": "proactive",
    "reference": ["(V)", "(M2.3)"]
  }

  {
    "reasoning": "The user is searching for something with raw chicken nearby (V). From memory (M1.4), the fried chicken flour is in the cupboard. The user may ask, “Where is my fried chicken flour?”",
    "type": "passive",
    "reference": ["(V)", "(M1.4)"]
  }

  {
    "reasoning": "",
    "type": "nil",
    "reference": []
  }
  """
        if os.path.isfile(get_cache_path('reasoning')):
          with open(get_cache_path('reasoning'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    type_1_memories = {}
    for content in tqdm(contents, desc=f'{pid} generate_proactive', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      if video_id not in type_1_memories:
        type_1_memories[video_id] = []
      for i in range(len(content['type_1_memories'])):
        type_1_memories[video_id] += [content['type_1_memories'][i]]
      if f'{video_id}|{timestamp}' in cache:
        result += [cache[f'{video_id}|{timestamp}']]
        continue
      content['type_1_memories'] = type_1_memories[video_id].copy()
      user_prompt = {'type_1_memories': content['type_1_memories'], 'type_2_memories': content['type_2_memories']}
      try:
            video_frame = load_video(get_video_path(video_id), float(timestamp), 1)
        time_start = time.monotonic()
        output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, video_frame))
        time_end = time.monotonic()
      except RetryError:
        output = []
        time_start = time_end = 0
      except KeyError:
        output = []
        time_start = time_end = 0
      content['type_1_memories'] = type_1_memories[video_id].copy()
      output = {**content, 'result': output, 'delay': time_end - time_start}
      cache[f'{video_id}|{timestamp}'] = output
      result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('reasoning'), 'w') as file:
        file.write(json.dumps(cache, indent=2))
    return result


  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_proactive(contents: List[Any]) -> List[Any]:
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
        "urgency": <urgency>,
        "reference": ["<reference>", ...]
      },
      // Add more proactive-response interactions
    ]
  - Do _not_ use any triple-backtick fenced code blocks or language identifiers.

  INSTRUCTION:
  - Analyze the video and memories carefully. The video is an egocentric vision of me who uses an AI assistant.
  - "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
  - Find visual information. Generate proactive responses based on the visual information. Use given memories to analyze the degree of urgency.
  - Provide justification of why you need to interfere the current activity with a proactive response and why this is urgent. Only use the video and memories as the ground truth.
  - Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory.
  - Provide an urgency level from 0 to 10 that the assistant needs to interfere with the activity with a proactive response.
  - If there is no high quality proactive response, output an empty array.
  - If there are more than one high quality proactive response, output one question that has highest quality.

  EXAMPLE:
  [
    {
      "response": "You are allergic to milk. Please do not add any milk.",
      "justification": "The user is about to add milk to the chicken (V), but prior memory (M2.3) shows he is allergic to milk. The AI assistant should promptly remind him not to use it for marinating.",
      "urgency": 9,
      "reference": ["(M2.3)", "(V)"]
    }
  ]"""
        if os.path.isfile(get_cache_path('proactive')):
          with open(get_cache_path('proactive'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    type_1_memories = {}
    for content in tqdm(contents, desc=f'{pid} generate_proactive', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      if video_id not in type_1_memories:
        type_1_memories[video_id] = []
      for i in range(len(content['type_1_memories'])):
        type_1_memories[video_id] += [content['type_1_memories'][i]]
      if f'{video_id}|{timestamp}' in cache:
        result += [cache[f'{video_id}|{timestamp}']]
        continue
      content['type_1_memories'] = type_1_memories[video_id].copy()
      user_prompt = {'type_1_memories': content['type_1_memories'], 'type_2_memories': content['type_2_memories']}
      try:
            video_frame = load_video(get_video_path(video_id), float(timestamp), 1)
        time_start = time.monotonic()
        output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, video_frame))
        time_end = time.monotonic()
      except RetryError:
        output = []
        time_start = time_end = 0
      except KeyError:
        output = []
        time_start = time_end = 0
      content['type_1_memories'] = type_1_memories[video_id].copy()
      output = {**content, 'result': output, 'delay': time_end - time_start}
      cache[f'{video_id}|{timestamp}'] = output
      result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('proactive'), 'w') as file:
        file.write(json.dumps(cache, indent=2))
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_classification(contents: List[Any]) -> List[Any]:
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
  - Analyze the video and memories carefully. The video is an egocentric vision of me who uses an AI assistant.
  - "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
  - Determine whether it is time to speak proactively or keep silent. Keep the balance between providing useful information or prevention and annoyance of the user.
  - Return "speak" or "silent" in the token field."""
        if os.path.isfile(get_cache_path('classification')):
          with open(get_cache_path('classification'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    for content in tqdm(contents, desc=f'{pid} generate_classification', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      if f'{video_id}|{timestamp}' in cache:
        result += [cache[f'{video_id}|{timestamp}']]
        continue
      user_prompt = {'type_1_memories': content['type_1_memories'], 'type_2_memories': content['type_2_memories']}
      output = {'token': 'invalid'}
      # try:
      #   video_frame = load_video(f'{args.ego4d_path}/v2/{args.ego4d_dataset}/{video_id}.mp4', float(timestamp), 1)
      #   output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, video_frame))
      # except RetryError:
      #   output = {'token': 'invalid'}
      # except KeyError:
      #   output = {'token': 'invalid'}
      output = {**content, 'token': output['token']}
      cache[f'{video_id}|{timestamp}'] = output
      result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('classification'), 'w') as file:
        file.write(json.dumps(cache, indent=2))
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_ans_and_grd(contents: List) -> List[Any]:
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
  - Give correctness mark in floating point from 0 to 10 where 0 is absolutely incorrect and 10 is absolutely correct."""
        if os.path.isfile(get_cache_path('ans_and_grd')):
          with open(get_cache_path('ans_and_grd'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    for content in tqdm(contents, desc=f'{pid} generate_ans_and_grd', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      caption = content['caption']
      memories = content['type_1_memories'] + content['type_2_memories']
      for qa in content['result']:
        question = qa['question']
        correct_answer = qa['answer']
        if f'{video_id}|{timestamp}|{question}' in cache:
          result += [cache[f'{video_id}|{timestamp}|{question}']]
          continue
        user_prompt = {'caption': caption, 'memories': memories, 'question': question}
        try:
            vt1t2_answer = run_inference(args.model_id, supply_completion(ans_prompt, user_prompt, load_video(get_video_path(video_id), float(timestamp), 1)))['answer']
        except RetryError:
          vt1t2_answer = 'No answer'
        except KeyError:
          vt1t2_answer = 'No answer'
        user_prompt = {'caption': caption, 'memories': [], 'question': question}
        try:
            v_answer = run_inference(args.model_id, supply_completion(ans_prompt, user_prompt, load_video(get_video_path(video_id), float(timestamp), 1)))['answer']
        except RetryError:
          v_answer = 'No answer'
        except KeyError:
          v_answer = 'No answer'
        user_prompt = {'question': question, 'correct_answer': correct_answer, 'answer': vt1t2_answer}
        try:
          vt1t2_grd = run_inference(args.model_id, supply_completion(grd_prompt, user_prompt, []))
          vt1t2_correctness = vt1t2_grd['correctness']
          vt1t2_justification = vt1t2_grd['justification']
        except RetryError:
          vt1t2_correctness = 0
          vt1t2_justification = 'NA'
        user_prompt = {'question': question, 'correct_answer': correct_answer, 'answer': v_answer}
        if vt1t2_correctness > 8:
          try:
            v_grd = run_inference(args.model_id, supply_completion(grd_prompt, user_prompt, []))
            v_correctness = v_grd['correctness']
            v_justification = v_grd['justification']
          except RetryError:
            v_correctness = 0
            v_justification = 'NA'
        else:
          v_correctness = 0
          v_justification = 'NA'
        output = {**content, **qa, 'vt1t2_answer': vt1t2_answer, 'vt1t2_correctness': vt1t2_correctness, 'vt1t2_justification': vt1t2_justification, 'v_answer': v_answer, 'v_correctness': v_correctness, 'v_justification': v_justification}
        output.pop('result')  
        cache[f'{video_id}|{timestamp}|{question}'] = output
        result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('ans_and_grd'), 'w') as file:
          file.write(json.dumps(cache, indent=2))
        os.makedirs(cache_dir, exist_ok=True)
        with open(get_cache_path('ans_and_grd'), 'w') as file:
      file.write(json.dumps(cache, indent=2))
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_proactive_evaluation(contents: List) -> List[Any]:
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
  - Analyze the video and memories carefully. The video is an egocentric vision of me who uses an AI assistant.
  - "type_1_memories" is a list of memories that happen during the video, and "type_2_memories" is a list of speculative histories of items.
  - Give score from 0 to 5 whether the proactive response is necessary. You can assume that the response is given at the end of the clip. Provide justification on your scoring.
  - Use "(Mx.y)" format to refer a memory where "x" is the type of the memory and "y" is the index of the memory in one-base. Use "(V)" to refer a video. Do _not_ use another format to refer a memory."""
        if os.path.isfile(get_cache_path('proactive_evaluation')):
          with open(get_cache_path('proactive_evaluation'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    for content in tqdm(contents, desc=f'{pid} generate_proactive_evaluation', leave=False):
      video_id = content['video_id']
      timestamp = content['timestamp']
      caption = content['caption']
      t1_memories = content['type_1_memories']
      t2_memories = content['type_2_memories']
      for proactive in content['result']:
        response = proactive['response']
        urgency = proactive['urgency']
        if urgency < 8:
          continue
        user_prompt = {'caption': caption, 'type_1_memories': t1_memories, 'type_2_memories': t2_memories, 'response': response}
        # output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, load_video(f'{args.ego4d_path}/v2/{args.ego4d_dataset}/{video_id}.mp4', float(timestamp), 1)))
        output = {**content, 'response': response, 'urgency': urgency, 'justification': proactive['justification'], 'reference': proactive['reference']}
        output.pop('result')  
        cache[f'{video_id}|{timestamp}|{response}'] = output
        result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('proactive_evaluation'), 'w') as file:
          file.write(json.dumps(cache, indent=2))
    return result

  @retry(stop=stop_after_attempt(15000), wait=wait_random(min=2, max=4))
  def generate_justfication_understanding(contents: List) -> List[Any]:
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
        if os.path.isfile(get_cache_path('justification_understanding')):
          with open(get_cache_path('justification_understanding'), 'r') as file:
        cache = json.loads(file.read())
    else:
      cache = {}
    result = []
    for content in tqdm(contents, desc=f'{pid} generate_justification_understanding', leave=False):
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
        user_prompt = {'reasoning': response}
        # output = run_inference(args.model_id, supply_completion(sys_prompt, user_prompt, load_video(f'{args.ego4d_path}/v2/{args.ego4d_dataset}/{video_id}.mp4', float(timestamp), 1)))
        output = {**content, 'response': response}
        output.pop('result')  
        cache[f'{video_id}|{timestamp}|{response}'] = output
        result += [output]
          os.makedirs(cache_dir, exist_ok=True)
          with open(get_cache_path('justification_understanding'), 'w') as file:
          file.write(json.dumps(cache, indent=2))
    return result

      def supply_completion(sys_prompt: str, user_prompt: Dict[str, Any], frames: List[Any]) -> List[Dict[str, str]]:
    return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": json.dumps(user_prompt)}] + \
      ([{"role": "user", "content": build_image_messages(frames)}] if len(frames) else [])

      @retry(stop=stop_after_attempt(13), wait=wait_random(min=2, max=4))
      def run_inference(model, input) -> str:
        # print('run_inference')
        try:
          response = openai.ChatCompletion.create(model=model, messages=input)
          output = json.loads(response.choices[0].message.content)
          return output
        except Exception as e:
          # print(e)
          # print(e.http_body)
          # import traceback
          # traceback.print_exc()
          raise EOFError

      def load_video(video_path: str, timestamp: float, target_fps: int) -> List[str]:
        frames = sample_frames(video_path, timestamp - args.video_start_before, timestamp + args.video_end_after, target_fps)
        b64_images  = [frame_to_base64_png(f) for f in frames]
        return b64_images

      def sample_frames(path: str, start: float, end: float, target_fps: float) -> List[Any]:
        frames, _, info = torchvision.io.read_video(path, start_pts=start, end_pts=end, pts_unit="sec")
        native_fps = info["video_fps"]
        stride = max(int(round(native_fps / target_fps)), 1)
        sampled = frames[::stride]
        return [frame for frame in sampled]

      def frame_to_base64_png(frame: Any) -> str:
        img = Image.fromarray(frame.numpy())
        (width, height) = (img.width // args.scale_down, img.height // args.scale_down)
        img_new = img.resize((width, height))
        buf = io.BytesIO()
        img_new.save(buf, format="PNG", optimize=False)
        return base64.b64encode(buf.getvalue()).decode('utf-8')  

      def build_image_messages(b64_list: List[str]) -> List[Any]:
        return [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}} for b64 in b64_list]

      # print(f"============================ PID: {pid} ============================")
      timestamps = choose_timestamps(filter_annotations(filter_video_uids(pid)))
      t1_memories = generate_t1_memories(timestamps)
      t2_memories = generate_t2_memories(timestamps)
      merged = []
      # print(len(t1_memories))
      # print(len(t2_memories))
      assert(len(t1_memories) == len(t2_memories))
      for index in range(len(t1_memories)):
        # print(t1_memories[index]['video_id'], t2_memories[index]['video_id'], float(t1_memories[index]['timestamp']), float(t2_memories[index]['timestamp']))
        assert(t1_memories[index]['video_id'] == t2_memories[index]['video_id'])
        assert(abs(float(t1_memories[index]['timestamp']) - float(t2_memories[index]['timestamp'])) < 1e-5)
        temp = {'video_id': t1_memories[index]['video_id'], 'timestamp': t1_memories[index]['timestamp'], 'caption': t1_memories[index]['caption']}
        type_1_memories = []
        for item in t1_memories[index]['result']:
          type_1_memories += [item['persona']]
        type_2_memories = t2_memories[index]['result']
        temp['type_1_memories'] = type_1_memories
        temp['type_2_memories'] = type_2_memories
        merged += [temp]
      result = generate_ans_and_grd(generate_qa(merged))
      # generate_reasoning(merged)
      invalid = 0
      passed = []
      for item in result:
        if item['vt1t2_correctness'] < 6:
          invalid += 1
        elif item['v_correctness'] < 5:
          passed += [item]
      os.makedirs(passive_output_dir, exist_ok=True)
      os.makedirs(proactive_output_dir, exist_ok=True)
      print(f'{pid}: {invalid}/{len(result)} QA pair invalid')
      print(f'{pid}: {len(passed)}/{len(result)} QA pair generated')
      with open(os.path.join(passive_output_dir, str(pid)), 'w') as file:
        file.write(json.dumps(passed, indent=2))
      pv = generate_proactive_evaluation(generate_proactive(generate_classification(merged)))
      with open(os.path.join(proactive_output_dir, str(pid)), 'w') as file:
        file.write(json.dumps(pv, indent=2))

from multiprocessing import Pool

if __name__ == '__main__':
    args = parse_args()
    
    # Determine participant IDs
    if args.participant_ids:
        participant_ids = [int(pid.strip()) for pid in args.participant_ids.split(',')]
    else:
        # Default participant IDs (can be overridden via --participant_ids)
        participant_ids = [132, 137, 268, 293, 336, 9, 10, 11, 14, 15, 16, 17, 19, 20, 21, 24, 25, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 39, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 54, 55, 65, 66, 67, 74, 75, 77, 84, 86, 97, 98, 99, 102, 104, 110, 115, 116, 123, 124, 125, 126, 127, 128, 129, 133, 134, 135, 136, 138, 139, 141, 143, 145, 147, 148, 149, 153, 156, 157, 158, 159, 160, 161, 162, 166, 168, 169, 170, 171, 175, 178, 179, 183, 184, 187, 189, 190, 192, 195, 196, 197, 199, 202, 203, 204, 205, 206, 210, 211, 212, 213, 215, 220, 222, 223, 224, 238, 248, 249, 252, 254, 255, 256, 257, 259, 260, 261, 262, 263, 264, 265, 266, 267, 269, 270, 273, 277, 278, 281, 282, 283, 284, 286, 287, 288, 289, 290, 291, 292, 294, 297, 298, 302, 306, 310, 317, 320, 323, 324, 325, 326, 327, 328, 330, 331, 332, 333, 334, 337, 339, 355, 357, 359, 362, 364, 365, 367, 369, 371, 372, 373, 374, 375, 379, 381, 383, 396, 405, 420, 421, 422, 423, 425, 426, 427, 428, 429, 431, 432, 433, 434, 435, 436, 437, 438, 439, 441, 442, 443, 444, 447, 452, 453, 454, 455, 456, 459, 460, 462, 463, 464, 465, 466, 467, 468, 474, 481, 488, 491, 492, 494, 496, 504, 505, 506, 509, 518, 521, 525, 526, 527, 532, 536, 539, 541, 546, 548, 549, 551, 554, 555, 566, 567, 569, 570, 575, 576, 579, 580, 591, 592, 594, 595, 599, 601, 609, 619, 620, 624, 628, 629, 630, 633, 634, 639, 648, 652, 656, 658, 659, 670, 672, 676, 679, 680, 685, 686, 689, 690, 692, 693, 696, 697, 698, 699, 700, 701, 702, 703, 704, 709, 712, 713, 716, 733, 734, 753, 754, 755, 756, 757, 759, 760, 761, 762, 763, 764, 765, 766, 767, 768, 774, 783, 788, 791, 799, 800, 806, 809, 818, 822, 824, 826, 827, 832, 835, 838, 840, 843, 844, 846, 848, 850, 855, 856, 857, 860, 865, 866, 876, 12, 13, 18, 22, 23, 26, 27, 38, 40, 53, 56, 57, 58, 59, 60, 61, 62, 63, 64, 68, 69, 70, 71, 72, 73, 76, 78, 79, 80, 81, 82, 83, 85, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 100, 101, 103, 105, 106, 107, 108, 109, 111, 112, 113, 114, 118, 119, 120, 122, 130, 131, 140, 142, 144, 146, 150, 151, 152, 154, 155, 163, 164, 165, 167, 172, 173, 174, 176, 177, 180, 181, 182, 185, 186, 188, 191, 193, 194, 198, 200, 201, 207, 208, 209, 214, 216, 217, 218, 219, 221, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 239, 240, 241, 242, 243, 244, 245, 246, 247, 250, 251, 253, 258, 271, 272, 274, 275, 276, 279, 280, 285, 295, 296, 299, 300, 301, 303, 304, 305, 307, 311, 312, 314, 315, 316, 318, 321, 322, 329, 335, 338, 341, 342, 343, 346, 347, 348, 349, 350, 351, 353, 354, 356, 360, 361, 363, 366, 368, 370, 376, 377, 378, 380, 382, 384, 385, 386, 387, 388, 389, 390, 391, 392, 393, 394, 395, 397, 398, 399, 400, 401, 402, 403, 404, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 418, 419, 424, 430, 440, 445, 446, 448, 449, 450, 451, 458, 461, 469, 470, 471, 472, 475, 476, 477, 478, 479, 480, 482, 483, 484, 485, 486, 487, 489, 490, 493, 495, 497, 498, 499, 500, 501, 502, 503, 507, 508, 510, 511, 512, 513, 514, 515, 516, 517, 519, 520, 522, 523, 524, 528, 529, 530, 531, 533, 534, 535, 537, 538, 540, 542, 543, 544, 545, 547, 550, 552, 553, 556, 557, 558, 559, 560, 561, 562, 563, 564, 565, 568, 571, 572, 573, 574, 577, 578, 581, 582, 583, 584, 585, 586, 587, 588, 589, 590, 593, 596, 597, 598, 600, 602, 603, 604, 605, 606, 607, 608, 610, 611, 612, 613, 614, 615, 616, 617, 618, 621, 622, 623, 625, 626, 627, 631, 632, 635, 636, 637, 638, 640, 641, 642, 643, 644, 645, 646, 647, 649, 650, 651, 653, 654, 655, 657, 660, 661, 662, 663, 664, 665, 666, 667, 668, 669, 671, 673, 674, 675, 677, 678, 681, 684, 687, 688, 691, 694, 695, 705, 706, 707, 708, 710, 711, 714, 718, 719, 720, 721, 722, 724, 725, 726, 727, 728, 729, 730, 731, 735, 736, 737, 738, 739, 740, 741, 742, 743, 744, 745, 746, 758, 769, 770, 771, 772, 773, 775, 776, 777, 778, 779, 780, 781, 782, 784, 785, 786, 787, 789, 790, 792, 793, 794, 795, 796, 797, 798, 801, 802, 803, 804, 807, 808, 810, 811, 812, 813, 814, 815, 816, 817, 819, 820, 821, 823, 825, 828, 829, 830, 831, 833, 834, 836, 837, 839, 841, 842, 845, 847, 849, 851, 852, 853, 854, 858, 859, 861, 862, 863, 864, 867, 868, 869, 870, 871, 872, 873, 874, 875]
    
    print(f"Processing {len(participant_ids)} participants")
    
    # Process in parallel
    with Pool(processes=args.num_workers) as pool:
        pool.map(generate_enc, [(args, pid) for pid in participant_ids])